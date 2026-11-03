package handler

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"
)

func TestTenantInternalAuth(t *testing.T) {
	gin.SetMode(gin.TestMode)
	expectedKey := strings.Repeat("s", 48)

	for name, suppliedKey := range map[string]string{
		"missing": "",
		"wrong":   strings.Repeat("x", 48),
		"partial": strings.Repeat("s", 47),
	} {
		t.Run(name, func(t *testing.T) {
			called := false
			engine := gin.New()
			engine.GET("/v2/app/list", tenantInternalAuth(expectedKey), func(c *gin.Context) {
				called = true
				c.Status(http.StatusNoContent)
			})

			request := httptest.NewRequest(http.MethodGet, "/v2/app/list", nil)
			if suppliedKey != "" {
				request.Header.Set(tenantInternalKeyHeader, suppliedKey)
			}
			response := httptest.NewRecorder()
			engine.ServeHTTP(response, request)

			if response.Code != http.StatusUnauthorized {
				t.Fatalf("status = %d, want %d", response.Code, http.StatusUnauthorized)
			}
			if called {
				t.Fatal("protected handler was called")
			}
			if strings.Contains(response.Body.String(), expectedKey) ||
				strings.Contains(response.Body.String(), suppliedKey) && suppliedKey != "" {
				t.Fatal("credential value leaked in unauthorized response")
			}
		})
	}
}

func TestTenantInternalAuthAcceptsConfiguredKey(t *testing.T) {
	gin.SetMode(gin.TestMode)
	expectedKey := strings.Repeat("s", 48)
	engine := gin.New()
	engine.GET("/v2/app/list", tenantInternalAuth(expectedKey), func(c *gin.Context) {
		c.Status(http.StatusNoContent)
	})

	request := httptest.NewRequest(http.MethodGet, "/v2/app/list", nil)
	request.Header.Set(tenantInternalKeyHeader, expectedKey)
	response := httptest.NewRecorder()
	engine.ServeHTTP(response, request)

	if response.Code != http.StatusNoContent {
		t.Fatalf("status = %d, want %d", response.Code, http.StatusNoContent)
	}
}

func TestTenantInternalAuthFailsClosedWithoutServerCredential(t *testing.T) {
	gin.SetMode(gin.TestMode)
	engine := gin.New()
	engine.GET("/v2/app/list", tenantInternalAuth(""), func(c *gin.Context) {
		c.Status(http.StatusNoContent)
	})

	request := httptest.NewRequest(http.MethodGet, "/v2/app/list", nil)
	response := httptest.NewRecorder()
	engine.ServeHTTP(response, request)

	if response.Code != http.StatusUnauthorized {
		t.Fatalf("status = %d, want %d", response.Code, http.StatusUnauthorized)
	}
}
