//go:build linux || darwin

package config

import (
	"errors"
	"os"

	"golang.org/x/sys/unix"
)

// openCredentialFileNoFollow resolves and opens the credential in one kernel
// operation. O_NOFOLLOW prevents a path swap to a symbolic link between a
// separate path inspection and open; O_NONBLOCK prevents a hostile FIFO from
// blocking startup before the descriptor type is checked with fstat.
func openCredentialFileNoFollow(fileName string) (*os.File, error) {
	fd, err := unix.Open(
		fileName,
		unix.O_RDONLY|unix.O_CLOEXEC|unix.O_NOFOLLOW|unix.O_NONBLOCK,
		0,
	)
	if err != nil {
		if errors.Is(err, unix.ELOOP) {
			return nil, errors.New(
				"credential file must be a regular non-symbolic-link file",
			)
		}
		return nil, errors.New("credential file is unavailable")
	}
	return os.NewFile(uintptr(fd), fileName), nil
}
