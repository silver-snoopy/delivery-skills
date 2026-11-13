import sys


def main() -> int:
    from ledger import cli  # imported lazily so the package loads without it

    return cli.main(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
