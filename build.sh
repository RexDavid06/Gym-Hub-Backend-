#!/usr/bin/env bash
set -o errexit
set -o pipefail
set -o nounset

pip install -r requirements/prod.txt
python manage.py check --deploy --settings=config.settings.prod
python manage.py collectstatic --noinput --settings=config.settings.prod
