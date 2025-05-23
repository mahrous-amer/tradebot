#!/bin/bash

extensions=("pg_trgm" "timescaledb" "hstore")

echo "Starting to create extensions..."

for ext in "${extensions[@]}"; do
    echo "Creating extension: $ext"
    
    if psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<EOF
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = '$ext') THEN
            CREATE EXTENSION "$ext";
        END IF;
    END
    \$\$;
EOF
    then
        echo "Successfully created extension: $ext"
    else
        echo "Error creating extension: $ext" >&2
        exit 1
    fi
done

echo "Installed extensions:"
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -c "SELECT * FROM pg_extension;"
