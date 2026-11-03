package handler

import (
	"crypto/sha256"
	"crypto/subtle"
	"log"
	"net/http"
	"strconv"

	"tenant/config"
	"tenant/internal/dao"
	"tenant/internal/service"
	"tenant/tools/database"
	"tenant/tools/generator"

	"github.com/gin-gonic/gin"
)

var (
	sidGenerator2 = &generator.SidGenerator2{}
	appHandler    *AppHandler
	authHandler   *AuthHandler
	keySid        = "sid"
	keySource     = "source"
)

const tenantInternalKeyHeader = "X-Tenant-Internal-Key"

func InitRouter(e *gin.Engine, conf *config.Config) error {
	err := initHandler(conf)
	if err != nil {
		return err
	}
	appGroup := e.Group("/v2/app")
	appGroup.Use(tenantInternalAuth(conf.TenantBootstrap.Secret), preProcess)
	appGroup.POST("", appHandler.SaveApp)
	appGroup.PUT("", appHandler.ModifyApp)
	appGroup.GET("/list", appHandler.ListApp)
	appGroup.GET("/details", appHandler.DetailApp)
	appGroup.POST("/disable", appHandler.DisableApp)
	appGroup.DELETE("", appHandler.DeleteApp)

	authGroup := e.Group("/v2/app/key")
	authGroup.Use(tenantInternalAuth(conf.TenantBootstrap.Secret), preProcess)
	authGroup.POST("", authHandler.SaveAuth)
	authGroup.DELETE("", authHandler.DeleteAuth)
	authGroup.POST("/verify", authHandler.VerifyAppAuth)
	authGroup.GET("/:app_id", authHandler.ListAuth)
	authGroup.GET("/api_key/:api_key", authHandler.GetAppByAPIKey)

	sidGenerator2.Init(conf.Server.Location, generator.IP, strconv.Itoa(conf.Server.Port))
	return nil
}

func tenantInternalAuth(expectedKey string) gin.HandlerFunc {
	return func(c *gin.Context) {
		suppliedKey := c.GetHeader(tenantInternalKeyHeader)
		expectedDigest := sha256.Sum256([]byte(expectedKey))
		suppliedDigest := sha256.Sum256([]byte(suppliedKey))
		if expectedKey == "" || suppliedKey == "" || subtle.ConstantTimeCompare(
			suppliedDigest[:], expectedDigest[:],
		) != 1 {
			c.AbortWithStatusJSON(
				http.StatusUnauthorized,
				newErrResp(UnauthorizedErr, "unauthorized", ""),
			)
			return
		}
		c.Next()
	}
}

func initHandler(conf *config.Config) error {
	db, err := database.NewDatabase(conf)
	if err != nil {
		return err
	}
	appDao, err := dao.NewAppDao(db)
	if err != nil {
		return err
	}
	authDao, err := dao.NewAuthDao(db)
	if err != nil {
		return err
	}
	appService, err := service.NewAppService(appDao, authDao)
	if err != nil {
		return err
	}
	authService, err := service.NewAuthService(appDao, authDao)
	if err != nil {
		return err
	}
	appHandler, err = NewAppHandler(appService)
	if err != nil {
		return err
	}
	authHandler, err = NewAuthHandler(authService)
	if err != nil {
		return err
	}
	return nil
}

func preProcess(c *gin.Context) {
	sid, err := sidGenerator2.NewSid("app")
	if err != nil {
		log.Printf("generate sid error: %v", err)
		resp := newErrResp(SidErr, err.Error(), "generate sid error")
		c.JSON(http.StatusOK, resp)
		return
	}
	source := c.Request.Header.Get("X-Consumer-Username")
	if len(source) == 0 {
		source = "admin"
	}
	c.Set(keySource, source)
	c.Set(keySid, sid)
	c.Next()
}
