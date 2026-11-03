package database

import (
	"context"
	"crypto/subtle"
	"database/sql"
	"errors"
	"fmt"
	"time"

	"tenant/config"
)

const tenantBootstrapManagedMarker = "astron-bootstrap-managed-v1"

type bootstrapRowScanner interface {
	Scan(dest ...any) error
}

type bootstrapTransaction interface {
	ExecContext(context.Context, string, ...any) (sql.Result, error)
	QueryRowContext(context.Context, string, ...any) bootstrapRowScanner
}

type sqlBootstrapTransaction struct {
	transaction *sql.Tx
}

func (transaction sqlBootstrapTransaction) ExecContext(
	ctx context.Context,
	query string,
	args ...any,
) (sql.Result, error) {
	return transaction.transaction.ExecContext(ctx, query, args...)
}

func (transaction sqlBootstrapTransaction) QueryRowContext(
	ctx context.Context,
	query string,
	args ...any,
) bootstrapRowScanner {
	return transaction.transaction.QueryRowContext(ctx, query, args...)
}

func reconcileTenantBootstrap(
	client *sql.DB,
	credentials config.TenantBootstrapCredentials,
) error {
	if client == nil {
		return errors.New("mysql client is nil")
	}
	if err := credentials.Validate(); err != nil {
		return fmt.Errorf("invalid tenant bootstrap credentials: %w", err)
	}

	ctx := context.Background()
	transaction, err := client.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("begin tenant bootstrap transaction failed: %w", err)
	}
	defer func() {
		_ = transaction.Rollback()
	}()

	if err := reconcileTenantBootstrapTransaction(
		ctx,
		sqlBootstrapTransaction{transaction: transaction},
		credentials,
	); err != nil {
		return err
	}
	if err := transaction.Commit(); err != nil {
		return fmt.Errorf("commit tenant bootstrap transaction failed: %w", err)
	}
	return nil
}

func reconcileTenantBootstrapTransaction(
	ctx context.Context,
	transaction bootstrapTransaction,
	credentials config.TenantBootstrapCredentials,
) error {
	if transaction == nil {
		return errors.New("tenant bootstrap transaction is nil")
	}
	if err := credentials.Validate(); err != nil {
		return fmt.Errorf("invalid tenant bootstrap credentials: %w", err)
	}

	now := time.Now().Format("2006-01-02 15:04:05")
	if err := ensureAndLockTenantBootstrapApp(ctx, transaction, credentials, now); err != nil {
		return err
	}

	adoptExistingUnmanagedCredential, err := findTenantBootstrapCredential(
		ctx,
		transaction,
		credentials,
	)
	if err != nil {
		return err
	}
	if adoptExistingUnmanagedCredential {
		if err := adoptTenantBootstrapCredential(ctx, transaction, credentials, now); err != nil {
			return err
		}
	}
	return rotateTenantBootstrapCredentials(ctx, transaction, credentials, now)
}

func ensureAndLockTenantBootstrapApp(
	ctx context.Context,
	transaction bootstrapTransaction,
	credentials config.TenantBootstrapCredentials,
	now string,
) error {
	if _, err := transaction.ExecContext(
		ctx,
		`INSERT IGNORE INTO tb_app
  (update_time, registration_time, app_id, app_name, dev_id, channel_id, source, is_disable, app_desc, is_delete, extend)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		now,
		now,
		credentials.TenantID,
		"星辰租户",
		1,
		"0",
		"admin",
		false,
		"星辰租户",
		false,
		"",
	); err != nil {
		return fmt.Errorf("ensure tenant bootstrap app failed: %w", err)
	}

	// Serialize reconciliation across replicas on the reserved app row before
	// taking any auth-index gap locks or rotating managed credentials.
	var lockedAppID string
	var lockedAppDisabled sql.NullBool
	var lockedAppDeleted sql.NullBool
	if err := transaction.QueryRowContext(
		ctx,
		`SELECT app_id, is_disable, is_delete FROM tb_app WHERE app_id = ? FOR UPDATE`,
		credentials.TenantID,
	).Scan(&lockedAppID, &lockedAppDisabled, &lockedAppDeleted); err != nil {
		return fmt.Errorf("lock tenant bootstrap app failed: %w", err)
	}
	if lockedAppID != credentials.TenantID {
		return errors.New("locked tenant bootstrap app does not match the reserved tenant ID")
	}
	if !lockedAppDisabled.Valid || lockedAppDisabled.Bool ||
		!lockedAppDeleted.Valid || lockedAppDeleted.Bool {
		return errors.New("reserved tenant bootstrap app is disabled or deleted")
	}
	return nil
}

func findTenantBootstrapCredential(
	ctx context.Context,
	transaction bootstrapTransaction,
	credentials config.TenantBootstrapCredentials,
) (bool, error) {
	var collisionOwner string
	err := transaction.QueryRowContext(
		ctx,
		`SELECT app_id
FROM tb_auth
WHERE api_key = ? AND app_id <> ? AND is_delete = 0
LIMIT 1 FOR UPDATE`,
		credentials.APIKey,
		credentials.TenantID,
	).Scan(&collisionOwner)
	if err == nil {
		return false, errors.New("tenant bootstrap API key is already assigned to another active app")
	}
	if !errors.Is(err, sql.ErrNoRows) {
		return false, fmt.Errorf("check tenant bootstrap API key ownership failed: %w", err)
	}

	var unmanagedSecret sql.NullString
	var unmanagedIsDelete sql.NullBool
	err = transaction.QueryRowContext(
		ctx,
		`SELECT api_secret, is_delete
	FROM tb_auth
	WHERE app_id = ? AND api_key = ? AND COALESCE(extend, '') <> ?
	LIMIT 1 FOR UPDATE`,
		credentials.TenantID,
		credentials.APIKey,
		tenantBootstrapManagedMarker,
	).Scan(&unmanagedSecret, &unmanagedIsDelete)
	if err == nil {
		if !unmanagedSecret.Valid || !unmanagedIsDelete.Valid || unmanagedIsDelete.Bool ||
			subtle.ConstantTimeCompare([]byte(unmanagedSecret.String), []byte(credentials.Secret)) != 1 {
			return false, errors.New("tenant bootstrap API key conflicts with an unmanaged credential")
		}
		return true, nil
	}
	if err != nil && !errors.Is(err, sql.ErrNoRows) {
		return false, fmt.Errorf("check tenant bootstrap managed credential failed: %w", err)
	}
	return false, nil
}

func adoptTenantBootstrapCredential(
	ctx context.Context,
	transaction bootstrapTransaction,
	credentials config.TenantBootstrapCredentials,
	now string,
) error {
	// A strong pair explicitly configured by the deployment may already have
	// been created through Tenant's public API on an older release. Because it
	// belongs to the reserved app and exactly matches the current deployment
	// Secret, adopt it into managed ownership so a later rotation can retire it.
	if _, err := transaction.ExecContext(
		ctx,
		`UPDATE tb_auth
SET extend = ?, update_time = ?
WHERE app_id = ? AND api_key = ? AND api_secret = ? AND is_delete = 0
  AND COALESCE(extend, '') <> ?`,
		tenantBootstrapManagedMarker,
		now,
		credentials.TenantID,
		credentials.APIKey,
		credentials.Secret,
		tenantBootstrapManagedMarker,
	); err != nil {
		return fmt.Errorf("adopt tenant bootstrap credential failed: %w", err)
	}
	return nil
}

func rotateTenantBootstrapCredentials(
	ctx context.Context,
	transaction bootstrapTransaction,
	credentials config.TenantBootstrapCredentials,
	now string,
) error {
	if _, err := transaction.ExecContext(
		ctx,
		`UPDATE tb_auth
SET is_delete = 1, update_time = ?
WHERE api_key = ? AND api_secret = ?`,
		now,
		config.LegacyTenantKey,
		config.LegacyTenantSecret,
	); err != nil {
		return fmt.Errorf("disable published legacy tenant credential failed: %w", err)
	}

	if _, err := transaction.ExecContext(
		ctx,
		`UPDATE tb_auth
SET is_delete = 1, update_time = ?
WHERE app_id = ? AND extend = ? AND api_key <> ?`,
		now,
		credentials.TenantID,
		tenantBootstrapManagedMarker,
		credentials.APIKey,
	); err != nil {
		return fmt.Errorf("retire previous managed tenant credential failed: %w", err)
	}
	if _, err := transaction.ExecContext(
		ctx,
		`INSERT INTO tb_auth
  (update_time, registration_time, app_id, api_key, api_secret, source, is_delete, extend)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON DUPLICATE KEY UPDATE
  api_secret = VALUES(api_secret),
  source = VALUES(source),
  is_delete = VALUES(is_delete),
  update_time = VALUES(update_time),
  extend = VALUES(extend)`,
		now,
		now,
		credentials.TenantID,
		credentials.APIKey,
		credentials.Secret,
		0,
		false,
		tenantBootstrapManagedMarker,
	); err != nil {
		return fmt.Errorf("upsert managed tenant credential failed: %w", err)
	}

	return nil
}
