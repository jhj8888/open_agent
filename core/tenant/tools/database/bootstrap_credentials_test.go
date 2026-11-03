package database

import (
	"context"
	"database/sql"
	"errors"
	"strings"
	"testing"

	"tenant/config"
)

type recordedBootstrapExecution struct {
	query string
	args  []any
}

type fakeBootstrapTransaction struct {
	rows       []bootstrapRowScanner
	executions []recordedBootstrapExecution
	execError  error
}

func (transaction *fakeBootstrapTransaction) ExecContext(
	_ context.Context,
	query string,
	args ...any,
) (sql.Result, error) {
	transaction.executions = append(transaction.executions, recordedBootstrapExecution{
		query: query,
		args:  args,
	})
	if transaction.execError != nil {
		return nil, transaction.execError
	}
	return fakeBootstrapResult(1), nil
}

func (transaction *fakeBootstrapTransaction) QueryRowContext(
	_ context.Context,
	_ string,
	_ ...any,
) bootstrapRowScanner {
	if len(transaction.rows) == 0 {
		return fakeBootstrapRow{err: sql.ErrNoRows}
	}
	row := transaction.rows[0]
	transaction.rows = transaction.rows[1:]
	return row
}

type fakeBootstrapRow struct {
	value  string
	values []any
	err    error
}

func (row fakeBootstrapRow) Scan(dest ...any) error {
	if row.err != nil {
		return row.err
	}
	values := row.values
	if values == nil {
		values = []any{row.value}
	}
	if len(dest) != len(values) {
		return errors.New("unexpected scan destination count")
	}
	for index, destination := range dest {
		if err := assignFakeBootstrapValue(values[index], destination); err != nil {
			return err
		}
	}
	return nil
}

func assignFakeBootstrapValue(value any, destination any) error {
	switch target := destination.(type) {
	case *string:
		return assignFakeString(target, value)
	case *sql.NullString:
		return assignFakeNullString(target, value)
	case *sql.NullBool:
		return assignFakeNullBool(target, value)
	default:
		return errors.New("unexpected scan destination type")
	}
}

func assignFakeString(target *string, value any) error {
	text, ok := value.(string)
	if !ok {
		return errors.New("unexpected string scan value")
	}
	*target = text
	return nil
}

func assignFakeNullString(target *sql.NullString, value any) error {
	text, ok := value.(string)
	if !ok {
		return errors.New("unexpected nullable string scan value")
	}
	*target = sql.NullString{String: text, Valid: true}
	return nil
}

func assignFakeNullBool(target *sql.NullBool, value any) error {
	boolean, ok := value.(bool)
	if !ok {
		return errors.New("unexpected nullable bool scan value")
	}
	*target = sql.NullBool{Bool: boolean, Valid: true}
	return nil
}

type fakeBootstrapResult int64

func (result fakeBootstrapResult) LastInsertId() (int64, error) {
	return 0, nil
}

func (result fakeBootstrapResult) RowsAffected() (int64, error) {
	return int64(result), nil
}

func testBootstrapCredentials() config.TenantBootstrapCredentials {
	return config.TenantBootstrapCredentials{
		TenantID: config.BootstrapTenantID,
		APIKey:   strings.Repeat("k", 48),
		Secret:   strings.Repeat("s", 48),
	}
}

func assertBootstrapQueriesDoNotInterpolateCredentials(
	t *testing.T,
	executions []recordedBootstrapExecution,
	credentials config.TenantBootstrapCredentials,
) {
	t.Helper()
	for _, execution := range executions {
		if strings.Contains(execution.query, credentials.APIKey) ||
			strings.Contains(execution.query, credentials.Secret) ||
			strings.Contains(execution.query, config.LegacyTenantKey) ||
			strings.Contains(execution.query, config.LegacyTenantSecret) {
			t.Fatal("credential value was interpolated into SQL instead of passed as a parameter")
		}
	}
}

func assertLegacyCredentialRevocation(
	t *testing.T,
	execution recordedBootstrapExecution,
) {
	t.Helper()
	if strings.Contains(execution.query, "app_id") {
		t.Fatal("published legacy credential revocation must apply globally, not only to the reserved app")
	}
	if execution.args[1] != config.LegacyTenantKey || execution.args[2] != config.LegacyTenantSecret {
		t.Fatalf("legacy disable arguments = %#v, want exact published pair", execution.args)
	}
}

func assertManagedCredentialRetirement(
	t *testing.T,
	execution recordedBootstrapExecution,
	credentials config.TenantBootstrapCredentials,
) {
	t.Helper()
	if execution.args[1] != config.BootstrapTenantID ||
		execution.args[2] != tenantBootstrapManagedMarker ||
		execution.args[3] != credentials.APIKey {
		t.Fatalf("managed retirement arguments = %#v, want reserved app and marker", execution.args)
	}
}

func assertManagedCredentialUpsert(
	t *testing.T,
	execution recordedBootstrapExecution,
	credentials config.TenantBootstrapCredentials,
) {
	t.Helper()
	if execution.args[2] != config.BootstrapTenantID ||
		execution.args[3] != credentials.APIKey ||
		execution.args[4] != credentials.Secret ||
		execution.args[7] != tenantBootstrapManagedMarker {
		t.Fatalf("managed upsert arguments = %#v, want current managed credential", execution.args)
	}
}

func TestReconcileTenantBootstrapTransactionCreatesAndRotatesManagedCredential(t *testing.T) {
	transaction := &fakeBootstrapTransaction{
		rows: []bootstrapRowScanner{
			fakeBootstrapRow{values: []any{config.BootstrapTenantID, false, false}},
			fakeBootstrapRow{err: sql.ErrNoRows},
			fakeBootstrapRow{err: sql.ErrNoRows},
		},
	}
	credentials := testBootstrapCredentials()

	if err := reconcileTenantBootstrapTransaction(context.Background(), transaction, credentials); err != nil {
		t.Fatalf("reconcileTenantBootstrapTransaction() error = %v", err)
	}
	if len(transaction.executions) != 4 {
		t.Fatalf("execution count = %d, want app insert and three targeted auth writes", len(transaction.executions))
	}

	assertBootstrapQueriesDoNotInterpolateCredentials(t, transaction.executions, credentials)
	assertLegacyCredentialRevocation(t, transaction.executions[1])
	assertManagedCredentialRetirement(t, transaction.executions[2], credentials)
	assertManagedCredentialUpsert(t, transaction.executions[3], credentials)
}

func TestReconcileTenantBootstrapTransactionRejectsOtherAppKeyCollision(t *testing.T) {
	transaction := &fakeBootstrapTransaction{
		rows: []bootstrapRowScanner{
			fakeBootstrapRow{values: []any{config.BootstrapTenantID, false, false}},
			fakeBootstrapRow{value: "user-app"},
		},
	}

	err := reconcileTenantBootstrapTransaction(context.Background(), transaction, testBootstrapCredentials())
	if err == nil || !strings.Contains(err.Error(), "another active app") {
		t.Fatalf("reconcileTenantBootstrapTransaction() error = %v, want collision rejection", err)
	}
	if len(transaction.executions) != 1 {
		t.Fatalf("execution count = %d, want only rolled-back app ensure before collision", len(transaction.executions))
	}
}

func TestReconcileTenantBootstrapTransactionRejectsUnavailableReservedApp(t *testing.T) {
	for name, state := range map[string][]any{
		"disabled": {config.BootstrapTenantID, true, false},
		"deleted":  {config.BootstrapTenantID, false, true},
	} {
		t.Run(name, func(t *testing.T) {
			transaction := &fakeBootstrapTransaction{
				rows: []bootstrapRowScanner{fakeBootstrapRow{values: state}},
			}

			err := reconcileTenantBootstrapTransaction(
				context.Background(),
				transaction,
				testBootstrapCredentials(),
			)
			if err == nil || !strings.Contains(err.Error(), "disabled or deleted") {
				t.Fatalf(
					"reconcileTenantBootstrapTransaction() error = %v, want unavailable app rejection",
					err,
				)
			}
			if len(transaction.executions) != 1 {
				t.Fatalf(
					"execution count = %d, want no auth writes for unavailable app",
					len(transaction.executions),
				)
			}
		})
	}
}

func TestReconcileTenantBootstrapTransactionRejectsUnmanagedReservedKey(t *testing.T) {
	transaction := &fakeBootstrapTransaction{
		rows: []bootstrapRowScanner{
			fakeBootstrapRow{values: []any{config.BootstrapTenantID, false, false}},
			fakeBootstrapRow{err: sql.ErrNoRows},
			fakeBootstrapRow{values: []any{strings.Repeat("x", 48), false}},
		},
	}

	err := reconcileTenantBootstrapTransaction(context.Background(), transaction, testBootstrapCredentials())
	if err == nil || !strings.Contains(err.Error(), "unmanaged credential") {
		t.Fatalf("reconcileTenantBootstrapTransaction() error = %v, want unmanaged-row rejection", err)
	}
	if len(transaction.executions) != 1 {
		t.Fatalf("execution count = %d, want no auth writes after unmanaged collision", len(transaction.executions))
	}
}

func TestReconcileTenantBootstrapTransactionAdoptsMatchingUnmanagedPair(t *testing.T) {
	credentials := testBootstrapCredentials()
	transaction := &fakeBootstrapTransaction{
		rows: []bootstrapRowScanner{
			fakeBootstrapRow{values: []any{config.BootstrapTenantID, false, false}},
			fakeBootstrapRow{err: sql.ErrNoRows},
			fakeBootstrapRow{values: []any{credentials.Secret, false}},
		},
	}

	if err := reconcileTenantBootstrapTransaction(context.Background(), transaction, credentials); err != nil {
		t.Fatalf("reconcileTenantBootstrapTransaction() error = %v", err)
	}
	if len(transaction.executions) != 5 {
		t.Fatalf("execution count = %d, want app ensure, adoption, revocations, and managed upsert", len(transaction.executions))
	}
	adoption := transaction.executions[1]
	if !strings.Contains(adoption.query, "SET extend = ?") ||
		adoption.args[0] != tenantBootstrapManagedMarker ||
		adoption.args[2] != credentials.TenantID ||
		adoption.args[3] != credentials.APIKey ||
		adoption.args[4] != credentials.Secret {
		t.Fatalf("adoption execution = %#v, want exact pair marked as managed", adoption)
	}
	if !strings.Contains(transaction.executions[4].query, "ON DUPLICATE KEY UPDATE") {
		t.Fatal("adopted credential was not converged through the managed upsert")
	}
}

func TestReconcileTenantBootstrapTransactionRetiresAdoptedPairOnNextRotation(t *testing.T) {
	rotatedCredentials := testBootstrapCredentials()
	rotatedCredentials.APIKey = strings.Repeat("n", 48)
	rotatedCredentials.Secret = strings.Repeat("z", 48)
	transaction := &fakeBootstrapTransaction{
		rows: []bootstrapRowScanner{
			fakeBootstrapRow{values: []any{config.BootstrapTenantID, false, false}},
			fakeBootstrapRow{err: sql.ErrNoRows},
			fakeBootstrapRow{err: sql.ErrNoRows},
		},
	}

	if err := reconcileTenantBootstrapTransaction(
		context.Background(), transaction, rotatedCredentials,
	); err != nil {
		t.Fatalf("reconcileTenantBootstrapTransaction() error = %v", err)
	}
	managedRetirement := transaction.executions[2]
	if managedRetirement.args[1] != config.BootstrapTenantID ||
		managedRetirement.args[2] != tenantBootstrapManagedMarker ||
		managedRetirement.args[3] != rotatedCredentials.APIKey {
		t.Fatalf(
			"managed retirement arguments = %#v, want all prior adopted keys retired",
			managedRetirement.args,
		)
	}
}

func TestReconcileTenantBootstrapTransactionRejectsDeletedMatchingUnmanagedPair(t *testing.T) {
	credentials := testBootstrapCredentials()
	transaction := &fakeBootstrapTransaction{
		rows: []bootstrapRowScanner{
			fakeBootstrapRow{values: []any{config.BootstrapTenantID, false, false}},
			fakeBootstrapRow{err: sql.ErrNoRows},
			fakeBootstrapRow{values: []any{credentials.Secret, true}},
		},
	}

	err := reconcileTenantBootstrapTransaction(context.Background(), transaction, credentials)
	if err == nil || !strings.Contains(err.Error(), "unmanaged credential") {
		t.Fatalf("reconcileTenantBootstrapTransaction() error = %v, want deleted-row rejection", err)
	}
}

func TestTenantInitialSchemaDoesNotSeedPublishedCredential(t *testing.T) {
	initialSchema := strings.Join(tenantInitStatements, "\n")
	if strings.Contains(initialSchema, config.LegacyTenantKey) ||
		strings.Contains(initialSchema, config.LegacyTenantSecret) {
		t.Fatal("tenant initialization still seeds the published credential")
	}
}
