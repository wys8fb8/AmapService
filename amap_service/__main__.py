"""Enable `python -m amap_service ...` without installing the console script."""
from amap_service.cli import main

if __name__ == "__main__":
    main()
