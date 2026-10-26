"""Start-up file for cPanel's "Setup Python App" (Phusion Passenger).

Passenger speaks WSGI and the office is an ASGI application, so a2wsgi translates between them. In cPanel:

* Application root: the folder holding this project.
* Application startup file: ``deploy/cpanel/passenger_wsgi.py``. Entry point: ``application``.
* Run ``pip install -r requirements-cpanel.txt`` in the app's virtual environment, from the project folder.
* Put the settings from ``.env.example`` (DATABASE_URL, CLUB_ENV=production, CLUB_BASE_URL and the rest) in the app's
  environment variables, and add a cron job for ``python -m app.cli run-jobs`` every hour.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

from a2wsgi import ASGIMiddleware  # noqa: E402

from app.db import init_db  # noqa: E402
from app.main import app  # noqa: E402

init_db()  # Passenger does not run the application's start-up step, so the database is made ready here
application = ASGIMiddleware(app)
