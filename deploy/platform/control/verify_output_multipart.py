"""Run only the real incomplete-multipart investigation on disposable services."""
from verify_output_reconciliation import main

if __name__ == "__main__":
    main(multipart_only=True)
