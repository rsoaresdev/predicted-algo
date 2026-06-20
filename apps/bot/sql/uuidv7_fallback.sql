-- UUIDv7 fallback for PostgreSQL < 18 (PostgreSQL 18 ships uuidv7() natively).
-- database/db.py installs this automatically when it detects an older server;
-- this file is kept for reference / manual installation.
--
-- Strategy: reuse gen_random_uuid() for the random + variant bits, overlay the
-- first 48 bits with the current Unix time in milliseconds, then set bits 52/53
-- to convert the v4 version nibble (0100) into v7 (0111).

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE OR REPLACE FUNCTION uuidv7() RETURNS uuid AS $$
BEGIN
    RETURN encode(
        set_bit(
            set_bit(
                overlay(
                    uuid_send(gen_random_uuid())
                    PLACING substring(
                        int8send(floor(extract(epoch FROM clock_timestamp()) * 1000)::bigint)
                        FROM 3
                    )
                    FROM 1 FOR 6
                ),
                52, 1
            ),
            53, 1
        ),
        'hex'
    )::uuid;
END;
$$ LANGUAGE plpgsql VOLATILE;
