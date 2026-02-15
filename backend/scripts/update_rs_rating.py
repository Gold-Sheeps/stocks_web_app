import sys
import os
import traceback

# Add the backend directory to sys.path to allow imports from app
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(current_dir)
sys.path.append(backend_dir)

from app.services.rs_service import RsService


def main():
    try:
        service = RsService()
        service.update_rs_ratings()
    except Exception:
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
