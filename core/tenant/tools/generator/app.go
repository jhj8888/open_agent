package generator

import (
	"bytes"
	cryptorand "crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"fmt"
	"io"
	"strconv"
	"strings"
	"time"

	"github.com/google/uuid"
)

var credentialRandomReader io.Reader = cryptorand.Reader

func GenCurrTime(format string) string {
	if len(format) == 0 {
		return time.Now().Format("2006-01-02 15:04:05")
	}
	return time.Now().Format(format)
}

func GenTimeByAdd(time time.Time, d time.Duration) string {
	return time.Add(d).Format("2006-01-02 15:04:05")
}

func GenKey(_ string) string {
	return hex.EncodeToString(mustReadCredentialRandomBytes(16))
}

func GenSecret() string {
	// 24 bytes provide 192 bits of entropy and encode to exactly 32 URL-safe
	// characters without padding or truncation.
	return base64.RawURLEncoding.EncodeToString(mustReadCredentialRandomBytes(24))
}

func mustReadCredentialRandomBytes(length int) []byte {
	data := make([]byte, length)
	if _, err := io.ReadFull(credentialRandomReader, data); err != nil {
		panic("secure credential random source failed")
	}
	return data
}

func GenAppId(num int) string {
	u := uuid.New()
	bf := bytes.Buffer{}
	bf.WriteString(strings.ReplaceAll(u.String(), "-", ""))
	bf.WriteString(strconv.Itoa(time.Now().Nanosecond()))
	return fmt.Sprintf("%x", sha256.Sum256(bf.Bytes()))[:num]
}
