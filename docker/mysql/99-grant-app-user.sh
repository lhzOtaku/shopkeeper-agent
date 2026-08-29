#!/bin/bash
set -eu

case "${MYSQL_USER}" in
  *[!a-zA-Z0-9_]*)
    echo "MYSQL_USER may contain only letters, numbers, and underscores" >&2
    exit 1
    ;;
esac

mysql --protocol=socket -uroot -p"${MYSQL_ROOT_PASSWORD}" <<-EOSQL
GRANT ALL PRIVILEGES ON \`meta\`.* TO '${MYSQL_USER}'@'%';
GRANT ALL PRIVILEGES ON \`meta_v2\`.* TO '${MYSQL_USER}'@'%';
GRANT ALL PRIVILEGES ON \`dw\`.* TO '${MYSQL_USER}'@'%';
GRANT ALL PRIVILEGES ON \`dw_v2\`.* TO '${MYSQL_USER}'@'%';
FLUSH PRIVILEGES;
EOSQL
