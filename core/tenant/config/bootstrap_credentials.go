package config

import (
	"errors"
	"fmt"
	"io"
	"os"
	"strings"
	"unicode"
	"unicode/utf8"
)

const (
	BootstrapTenantID  = "680ab54f"
	LegacyTenantKey    = "7b709739e8da44536127a333c7603a83"
	LegacyTenantSecret = "NjhmY2NmM2NkZDE4MDFlNmM5ZjcyZjMy"

	tenantCredentialMinLength = 32
	tenantCredentialMaxLength = 50
	maxCredentialFileBytes    = 4096
)

// TenantBootstrapCredentials is the deployment-managed identity shared by
// Tenant and its internal callers. The values must never be logged.
type TenantBootstrapCredentials struct {
	TenantID string
	APIKey   string
	Secret   string
}

// LoadTenantBootstrapCredentials loads credentials from direct environment
// values or, when those are absent, bounded regular files. Direct values take
// precedence so Helm Secret-backed environment variables remain supported.
func LoadTenantBootstrapCredentials() (TenantBootstrapCredentials, error) {
	tenantID := strings.TrimSpace(os.Getenv("TENANT_ID"))
	if tenantID == "" {
		tenantID = BootstrapTenantID
	}

	apiKey, err := credentialFromEnvironmentOrFile("TENANT_KEY", "TENANT_KEY_FILE")
	if err != nil {
		return TenantBootstrapCredentials{}, err
	}
	secret, err := credentialFromEnvironmentOrFile("TENANT_SECRET", "TENANT_SECRET_FILE")
	if err != nil {
		return TenantBootstrapCredentials{}, err
	}

	credentials := TenantBootstrapCredentials{
		TenantID: tenantID,
		APIKey:   apiKey,
		Secret:   secret,
	}
	if err := credentials.Validate(); err != nil {
		return TenantBootstrapCredentials{}, err
	}
	return credentials, nil
}

// Validate enforces the storage and HTTP-header constraints shared by every
// bootstrap credential consumer.
func (credentials TenantBootstrapCredentials) Validate() error {
	if credentials.TenantID != BootstrapTenantID {
		return fmt.Errorf(
			"TENANT_ID must remain %s because persisted bootstrap data refers to it",
			BootstrapTenantID,
		)
	}
	if err := validateCredential("TENANT_KEY", credentials.APIKey); err != nil {
		return err
	}
	if err := validateCredential("TENANT_SECRET", credentials.Secret); err != nil {
		return err
	}
	if credentials.APIKey == credentials.Secret {
		return errors.New("TENANT_KEY and TENANT_SECRET must be distinct values")
	}
	if credentials.APIKey == LegacyTenantKey || credentials.Secret == LegacyTenantSecret {
		return errors.New("published legacy tenant credentials cannot be used")
	}
	return nil
}

func credentialFromEnvironmentOrFile(valueEnvironment, fileEnvironment string) (string, error) {
	if value := strings.TrimSpace(os.Getenv(valueEnvironment)); value != "" {
		if err := validateCredential(valueEnvironment, value); err != nil {
			return "", err
		}
		return value, nil
	}

	fileName := strings.TrimSpace(os.Getenv(fileEnvironment))
	if fileName == "" {
		return "", fmt.Errorf("%s or %s is required", valueEnvironment, fileEnvironment)
	}
	value, err := readCredentialFile(fileName)
	if err != nil {
		return "", fmt.Errorf("load %s: %w", fileEnvironment, err)
	}
	if err := validateCredential(valueEnvironment, value); err != nil {
		return "", err
	}
	return value, nil
}

func readCredentialFile(fileName string) (string, error) {
	file, err := openCredentialFileNoFollow(fileName)
	if err != nil {
		return "", err
	}
	defer func() {
		_ = file.Close()
	}()

	openedInfo, err := file.Stat()
	if err != nil {
		return "", errors.New("credential file cannot be inspected")
	}
	if !openedInfo.Mode().IsRegular() {
		return "", errors.New("credential file must be a regular non-symbolic-link file")
	}
	if openedInfo.Size() > maxCredentialFileBytes {
		return "", errors.New("credential file is too large")
	}

	data, err := io.ReadAll(io.LimitReader(file, maxCredentialFileBytes+1))
	if err != nil {
		return "", errors.New("credential file cannot be read")
	}
	if len(data) > maxCredentialFileBytes {
		return "", errors.New("credential file is too large")
	}
	return strings.TrimSpace(string(data)), nil
}

func validateCredential(name, value string) error {
	length := utf8.RuneCountInString(value)
	if !utf8.ValidString(value) || length < tenantCredentialMinLength || length > tenantCredentialMaxLength {
		return fmt.Errorf("%s must contain 32-50 valid UTF-8 characters", name)
	}
	for _, character := range value {
		if unicode.IsControl(character) {
			return fmt.Errorf("%s must not contain control characters", name)
		}
		if !isSafeCredentialCharacter(character) {
			return fmt.Errorf("%s must contain only ASCII letters, digits, '.', '_', '~', or '-'", name)
		}
	}
	return nil
}

func isSafeCredentialCharacter(character rune) bool {
	return character >= 'a' && character <= 'z' ||
		character >= 'A' && character <= 'Z' ||
		character >= '0' && character <= '9' ||
		character == '.' || character == '_' || character == '~' || character == '-'
}
