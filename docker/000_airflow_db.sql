-- DB metadata cua Airflow, tach khoi DB nghiep vu `news`.
-- Chi chay mot lan, luc volume pgdata con trong (docker-entrypoint-initdb.d).
SELECT 'CREATE DATABASE airflow OWNER news'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'airflow')\gexec
