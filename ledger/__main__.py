import sys


def main() -> int:
    """Defer CLI import so the package loads without importing it at module level."""
    from ledger import cli

    return cli.main(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
