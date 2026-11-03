package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func validTenantBootstrapCredentials() TenantBootstrapCredentials {
	return TenantBootstrapCredentials{
		TenantID: BootstrapTenantID,
		APIKey:   strings.Repeat("k", 48),
		Secret:   strings.Repeat("s", 48),
	}
}

func clearTenantBootstrapEnvironment(t *testing.T) {
	t.Helper()
	for _, name := range []string{
		"TENANT_ID",
		"TENANT_KEY",
		"TENANT_KEY_FILE",
		"TENANT_SECRET",
		"TENANT_SECRET_FILE",
	} {
		t.Setenv(name, "")
	}
}

func TestLoadTenantBootstrapCredentialsFromEnvironment(t *testing.T) {
	clearTenantBootstrapEnvironment(t)
	t.Setenv("TENANT_KEY", strings.Repeat("k", 48))
	t.Setenv("TENANT_SECRET", strings.Repeat("s", 48))

	credentials, err := LoadTenantBootstrapCredentials()
	if err != nil {
		t.Fatalf("LoadTenantBootstrapCredentials() error = %v", err)
	}
	if credentials != validTenantBootstrapCredentials() {
		t.Fatalf("LoadTenantBootstrapCredentials() = %#v, want deployment credentials", credentials)
	}
}

func TestLoadTenantBootstrapCredentialsFromFiles(t *testing.T) {
	clearTenantBootstrapEnvironment(t)
	directory := t.TempDir()
	keyFile := filepath.Join(directory, "tenant-key")
	secretFile := filepath.Join(directory, "tenant-secret")
	if err := os.WriteFile(keyFile, []byte(strings.Repeat("k", 48)+"\n"), 0o400); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(secretFile, []byte(strings.Repeat("s", 48)+"\n"), 0o400); err != nil {
		t.Fatal(err)
	}
	t.Setenv("TENANT_KEY_FILE", keyFile)
	t.Setenv("TENANT_SECRET_FILE", secretFile)

	credentials, err := LoadTenantBootstrapCredentials()
	if err != nil {
		t.Fatalf("LoadTenantBootstrapCredentials() error = %v", err)
	}
	if credentials != validTenantBootstrapCredentials() {
		t.Fatalf("LoadTenantBootstrapCredentials() = %#v, want file credentials", credentials)
	}
}

func TestLoadTenantBootstrapCredentialsPrefersDirectValues(t *testing.T) {
	clearTenantBootstrapEnvironment(t)
	directory := t.TempDir()
	keyFile := filepath.Join(directory, "tenant-key")
	secretFile := filepath.Join(directory, "tenant-secret")
	if err := os.WriteFile(keyFile, []byte(strings.Repeat("x", 48)), 0o400); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(secretFile, []byte(strings.Repeat("y", 48)), 0o400); err != nil {
		t.Fatal(err)
	}
	t.Setenv("TENANT_KEY", strings.Repeat("k", 48))
	t.Setenv("TENANT_SECRET", strings.Repeat("s", 48))
	t.Setenv("TENANT_KEY_FILE", keyFile)
	t.Setenv("TENANT_SECRET_FILE", secretFile)

	credentials, err := LoadTenantBootstrapCredentials()
	if err != nil {
		t.Fatalf("LoadTenantBootstrapCredentials() error = %v", err)
	}
	if credentials != validTenantBootstrapCredentials() {
		t.Fatalf("direct environment credentials were not preferred: %#v", credentials)
	}
}

func TestLoadTenantBootstrapCredentialsRejectsSymbolicLink(t *testing.T) {
	clearTenantBootstrapEnvironment(t)
	directory := t.TempDir()
	target := filepath.Join(directory, "target")
	link := filepath.Join(directory, "tenant-key")
	secretFile := filepath.Join(directory, "tenant-secret")
	if err := os.WriteFile(target, []byte(strings.Repeat("k", 48)), 0o400); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(target, link); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(secretFile, []byte(strings.Repeat("s", 48)), 0o400); err != nil {
		t.Fatal(err)
	}
	t.Setenv("TENANT_KEY_FILE", link)
	t.Setenv("TENANT_SECRET_FILE", secretFile)

	if _, err := LoadTenantBootstrapCredentials(); err == nil || !strings.Contains(err.Error(), "non-symbolic-link") {
		t.Fatalf("LoadTenantBootstrapCredentials() error = %v, want symbolic-link rejection", err)
	}
}

func TestLoadTenantBootstrapCredentialsRejectsOversizedFile(t *testing.T) {
	clearTenantBootstrapEnvironment(t)
	directory := t.TempDir()
	keyFile := filepath.Join(directory, "tenant-key")
	secretFile := filepath.Join(directory, "tenant-secret")
	if err := os.WriteFile(keyFile, []byte(strings.Repeat("k", maxCredentialFileBytes+1)), 0o400); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(secretFile, []byte(strings.Repeat("s", 48)), 0o400); err != nil {
		t.Fatal(err)
	}
	t.Setenv("TENANT_KEY_FILE", keyFile)
	t.Setenv("TENANT_SECRET_FILE", secretFile)

	if _, err := LoadTenantBootstrapCredentials(); err == nil || !strings.Contains(err.Error(), "too large") {
		t.Fatalf("LoadTenantBootstrapCredentials() error = %v, want size rejection", err)
	}
}

func TestLoadTenantBootstrapCredentialsRejectsDirectory(t *testing.T) {
	clearTenantBootstrapEnvironment(t)
	directory := t.TempDir()
	secretFile := filepath.Join(directory, "tenant-secret")
	if err := os.WriteFile(secretFile, []byte(strings.Repeat("s", 48)), 0o400); err != nil {
		t.Fatal(err)
	}
	t.Setenv("TENANT_KEY_FILE", directory)
	t.Setenv("TENANT_SECRET_FILE", secretFile)

	if _, err := LoadTenantBootstrapCredentials(); err == nil || !strings.Contains(err.Error(), "regular") {
		t.Fatalf("LoadTenantBootstrapCredentials() error = %v, want directory rejection", err)
	}
}

func TestTenantBootstrapCredentialsValidate(t *testing.T) {
	tests := []struct {
		name        string
		credentials TenantBootstrapCredentials
		errorText   string
	}{
		{
			name:        "valid",
			credentials: validTenantBootstrapCredentials(),
		},
		{
			name: "wrong tenant id",
			credentials: TenantBootstrapCredentials{
				TenantID: "other",
				APIKey:   strings.Repeat("k", 48),
				Secret:   strings.Repeat("s", 48),
			},
			errorText: "must remain",
		},
		{
			name: "short key",
			credentials: TenantBootstrapCredentials{
				TenantID: BootstrapTenantID,
				APIKey:   strings.Repeat("k", 31),
				Secret:   strings.Repeat("s", 48),
			},
			errorText: "32-50",
		},
		{
			name: "line break",
			credentials: TenantBootstrapCredentials{
				TenantID: BootstrapTenantID,
				APIKey:   strings.Repeat("k", 40) + "\n" + strings.Repeat("k", 7),
				Secret:   strings.Repeat("s", 48),
			},
			errorText: "control",
		},
		{
			name: "authorization separator",
			credentials: TenantBootstrapCredentials{
				TenantID: BootstrapTenantID,
				APIKey:   strings.Repeat("k", 47) + ":",
				Secret:   strings.Repeat("s", 48),
			},
			errorText: "ASCII letters",
		},
		{
			name: "same values",
			credentials: TenantBootstrapCredentials{
				TenantID: BootstrapTenantID,
				APIKey:   strings.Repeat("k", 48),
				Secret:   strings.Repeat("k", 48),
			},
			errorText: "distinct",
		},
		{
			name: "legacy pair",
			credentials: TenantBootstrapCredentials{
				TenantID: BootstrapTenantID,
				APIKey:   LegacyTenantKey,
				Secret:   LegacyTenantSecret,
			},
			errorText: "legacy",
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			err := test.credentials.Validate()
			if test.errorText == "" {
				if err != nil {
					t.Fatalf("Validate() error = %v", err)
				}
				return
			}
			if err == nil || !strings.Contains(err.Error(), test.errorText) {
				t.Fatalf("Validate() error = %v, want text %q", err, test.errorText)
			}
		})
	}
}

func TestConfigStringOmitsTenantBootstrapCredentials(t *testing.T) {
	configuration := &Config{TenantBootstrap: validTenantBootstrapCredentials()}
	text := configuration.String()
	if strings.Contains(text, configuration.TenantBootstrap.APIKey) ||
		strings.Contains(text, configuration.TenantBootstrap.Secret) {
		t.Fatal("Config.String() exposed tenant bootstrap credentials")
	}
}
