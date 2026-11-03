//go:build !linux && !darwin

package config

import (
	"errors"
	"os"
)

// openCredentialFileNoFollow is a portability fallback for platforms without
// O_NOFOLLOW. Supported production images use the Unix implementation above.
func openCredentialFileNoFollow(fileName string) (*os.File, error) {
	pathInfo, err := os.Lstat(fileName)
	if err != nil {
		return nil, errors.New("credential file is unavailable")
	}
	if pathInfo.Mode()&os.ModeSymlink != 0 || !pathInfo.Mode().IsRegular() {
		return nil, errors.New(
			"credential file must be a regular non-symbolic-link file",
		)
	}
	file, err := os.Open(fileName)
	if err != nil {
		return nil, errors.New("credential file is unavailable")
	}
	openedInfo, err := file.Stat()
	if err != nil || !openedInfo.Mode().IsRegular() || !os.SameFile(pathInfo, openedInfo) {
		_ = file.Close()
		return nil, errors.New("credential file changed while being opened")
	}
	return file, nil
}
