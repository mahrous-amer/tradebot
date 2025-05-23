FROM postgres:16

RUN apt-get update && apt-get install -y postgresql-client-16 gnupg apt-transport-https lsb-release wget

RUN echo "deb https://packagecloud.io/timescale/timescaledb/debian/ $(lsb_release -c -s) main" | tee /etc/apt/sources.list.d/timescaledb.list

RUN wget --quiet -O - https://packagecloud.io/timescale/timescaledb/gpgkey | gpg --dearmor -o /etc/apt/trusted.gpg.d/timescaledb.gpg 

RUN apt-get update && apt-get install -y timescaledb-2-postgresql-16 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY load-extensions.sh /docker-entrypoint-initdb.d/

COPY ./*.sql /docker-entrypoint-initdb.d/

RUN timescaledb-tune --quiet --yes
