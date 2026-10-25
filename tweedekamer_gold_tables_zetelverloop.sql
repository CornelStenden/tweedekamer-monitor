-- ============================================================================
-- Tweede Kamer — Gold-layer SQL
-- De SQL achter de geëxporteerde CSV's:
--   dim_persoon.csv, dim_fractie.csv, fact_zetelwisseling.csv,
--   agg_zetelbeeld_per_jaar.csv
--
-- ZO GEBRUIK JE DIT BESTAND (DuckDB):
--   1. Koppel de brondatabase (tweedekamer.duckdb) read-only als "src":
--        ATTACH '<pad/naar/tweedekamer.duckdb>' AS src (READ_ONLY);
--      Op Databricks is dat pad:
--        /Volumes/workspace/tweede-kamer/data_files/tweedekamer.duckdb
--   2. Voer daarna de CREATE TABLE-statements hieronder uit (dim-tabellen
--      eerst, daarna de fact/agg-tabellen).
--      Run je de statements direct in de brondatabase zelf, vervang dan
--      overal "src.silver." door "silver.".
--
-- LET OP: PvdA, GL, GroenLinks-PvdA en PRO worden overal genormaliseerd
-- naar één fractie "PRO" (kolom fractie_norm).
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS gold;

-- ----------------------------------------------------------------------------
-- 1. gold.dim_persoon — wie (persoonsgegevens)
-- ----------------------------------------------------------------------------
CREATE OR REPLACE TABLE gold.dim_persoon AS
SELECT
    id,
    roepnaam,
    voornamen,
    tussenvoegsel,
    achternaam,
    geslacht,
    geboortedatum,
    fractielabel
FROM src.silver.persoon;

-- ----------------------------------------------------------------------------
-- 2. gold.dim_fractie — welke fractie (PRO-normalisatie + jaren_bestaan)
-- ----------------------------------------------------------------------------
CREATE OR REPLACE TABLE gold.dim_fractie AS
SELECT
    id,
    afkorting,
    naam_nl,
    CASE
        WHEN afkorting IN ('PvdA', 'GL', 'GroenLinks-PvdA', 'PRO') THEN 'PRO'
        ELSE afkorting
    END AS fractie_norm,
    aantal_zetels,
    datum_actief,
    datum_inactief,
    ROUND(
        GREATEST(
            DATE_DIFF('day', datum_actief, COALESCE(datum_inactief, DATE '2026-10-10')) / 365.25,
            0.01
        ),
        1
    ) AS jaren_bestaan
FROM src.silver.fractie;

-- ----------------------------------------------------------------------------
-- 3. gold.fact_zetelwisseling — één rij per event (aanmelding/vertrek)
--    De brug-tabel fractie_zetel is geflattened: fractie_id zit direct in de
--    feit-tabel. zetels_op_datum = aantal actieve leden op het event-moment.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE TABLE gold.fact_zetelwisseling AS
WITH aanmeldingen AS (
    SELECT
        fzp.persoon_id,
        f.id AS fractie_id,
        fzp.van AS datum_id,
        'aanmelding' AS event_type,
        CASE
            WHEN f.afkorting IN ('PvdA', 'GL', 'GroenLinks-PvdA', 'PRO') THEN 'PRO'
            ELSE f.afkorting
        END AS fractie_norm,
        EXTRACT(YEAR FROM fzp.van)::INTEGER AS jaar,
        GREATEST(
            DATE_DIFF('day', f.datum_actief, COALESCE(f.datum_inactief, DATE '2026-10-10')) / 365.25,
            0.01
        ) AS jaren_bestaan
    FROM src.silver.fractie_zetel_persoon fzp
    JOIN src.silver.fractie_zetel fz ON fz.id = fzp.fractie_zetel_id
    JOIN src.silver.fractie f ON f.id = fz.fractie_id
    WHERE fzp.van IS NOT NULL
),
vertrekken AS (
    SELECT
        fzp.persoon_id,
        f.id AS fractie_id,
        fzp.tot_en_met AS datum_id,
        'vertrek' AS event_type,
        CASE
            WHEN f.afkorting IN ('PvdA', 'GL', 'GroenLinks-PvdA', 'PRO') THEN 'PRO'
            ELSE f.afkorting
        END AS fractie_norm,
        EXTRACT(YEAR FROM fzp.tot_en_met)::INTEGER AS jaar,
        GREATEST(
            DATE_DIFF('day', f.datum_actief, COALESCE(f.datum_inactief, DATE '2026-10-10')) / 365.25,
            0.01
        ) AS jaren_bestaan
    FROM src.silver.fractie_zetel_persoon fzp
    JOIN src.silver.fractie_zetel fz ON fz.id = fzp.fractie_zetel_id
    JOIN src.silver.fractie f ON f.id = fz.fractie_id
    WHERE fzp.tot_en_met IS NOT NULL
),
all_events AS (
    SELECT * FROM aanmeldingen
    UNION ALL
    SELECT * FROM vertrekken
)
SELECT
    ROW_NUMBER() OVER (ORDER BY datum_id)::BIGINT AS event_id,
    persoon_id,
    fractie_id,
    datum_id,
    event_type,
    fractie_norm,
    jaar,
    ROUND(jaren_bestaan, 1) AS jaren_bestaan,
    -- zetels_op_datum: count active members in this fractie on the event date
    (
        SELECT COUNT(DISTINCT fzp2.persoon_id)
        FROM src.silver.fractie_zetel_persoon fzp2
        JOIN src.silver.fractie_zetel fz2 ON fz2.id = fzp2.fractie_zetel_id
        WHERE fz2.fractie_id = all_events.fractie_id
          AND fzp2.van <= all_events.datum_id
          AND (fzp2.tot_en_met IS NULL OR fzp2.tot_en_met >= all_events.datum_id)
    ) AS zetels_op_datum
FROM all_events
ORDER BY datum_id;

-- ----------------------------------------------------------------------------
-- 4. gold.agg_zetelbeeld_per_jaar — jaarlijkse zetelsnapshot per fractie
--    zetels = aantal personen met een actieve zetel in dat jaar
--    verandering = jaar-op-jaar verschil (voor de biggest-drop kaart)
-- ----------------------------------------------------------------------------
CREATE OR REPLACE TABLE gold.agg_zetelbeeld_per_jaar AS
WITH years AS (
    SELECT * FROM (VALUES (2000),(2001),(2002),(2003),(2004),(2005),(2006),(2007),(2008),(2009),
                         (2010),(2011),(2012),(2013),(2014),(2015),(2016),(2017),(2018),(2019),
                         (2020),(2021),(2022),(2023),(2024),(2025),(2026)) AS t(year_val)
),
yearly AS (
    SELECT
        CASE
            WHEN f.afkorting IN ('PvdA', 'GL', 'GroenLinks-PvdA', 'PRO') THEN 'PRO'
            ELSE f.afkorting
        END AS fractie_norm,
        y.year_val AS jaar,
        COUNT(DISTINCT fzp.persoon_id) AS zetels
    FROM src.silver.fractie f
    JOIN src.silver.fractie_zetel fz ON fz.fractie_id = f.id
    JOIN src.silver.fractie_zetel_persoon fzp ON fzp.fractie_zetel_id = fz.id
    CROSS JOIN years y
    WHERE EXTRACT(YEAR FROM fzp.van) <= y.year_val
      AND (fzp.tot_en_met IS NULL OR EXTRACT(YEAR FROM fzp.tot_en_met) >= y.year_val)
    GROUP BY 1, 2
)
SELECT
    fractie_norm,
    jaar,
    zetels,
    LAG(zetels) OVER (PARTITION BY fractie_norm ORDER BY jaar) AS zetels_vorig_jaar,
    zetels - LAG(zetels) OVER (PARTITION BY fractie_norm ORDER BY jaar) AS verandering
FROM yearly
ORDER BY fractie_norm, jaar;