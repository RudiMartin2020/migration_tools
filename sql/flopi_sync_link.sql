-- ============================================================================
-- flopi_sync_link : DB 링크(postgres_fdw) 기반 테이블 "완전 동기화" 함수
--
-- 동작:
--   1) 로컬에 테이블이 없으면  → 링크 테이블 구조로 CREATE (+ PRIMARY KEY)
--        - PK 는 자동 탐지: 링크 외부 테이블의 서버명을 카탈로그에서 찾아
--          dblink 로 원격 PK 를 조회 (p_pk_cols 수동 지정 시 그 값 우선)
--   2) 테이블이 있으면         → PK 기준 upsert
--        - 없는 행 INSERT
--        - 값이 달라진 행만 UPDATE (IS DISTINCT FROM — 불필요한 쓰기 방지)
--   3) p_prune = true 이면     → 링크에 없는 행을 로컬에서 삭제 (미러)
--        - upsert 보다 먼저 실행: 재키잉된 구행이 보조 UNIQUE 와 충돌하는 것 방지
--   4) PK 시퀀스(SERIAL/IDENTITY) 자동 탐지 후 최댓값으로 보정
--
-- 사전 준비 (1회):
--   CREATE EXTENSION postgres_fdw;
--   CREATE EXTENSION dblink;          -- 신규 생성 시 원격 PK 자동 탐지에 필요
--   CREATE SERVER <링크명> ... / CREATE USER MAPPING ... / IMPORT FOREIGN SCHEMA ...
--
-- 파라미터:
--   p_link_schema : 링크(외부 테이블) 스키마명
--   p_table       : 동기화 대상 테이블명
--   p_tgt_schema  : 로컬 대상 스키마 (기본: 현재 스키마)
--   p_pk_cols     : PK 컬럼 배열 (기본 NULL = 자동 탐지).
--                   자동 탐지 순서: 기존 테이블의 로컬 PK
--                     → 외부 테이블이면 dblink 로 원격 PK
--                     → 로컬 소스 테이블이면 소스 PK
--   p_prune       : true 면 링크에 없는 행 삭제 (기본 false)
--   p_skip_dup    : true 면 중복(PK·보조 UNIQUE 모두)인 행은 조용히 스킵하고
--                   계속 진행 (기본 false). 이 모드에선 UPDATE 하지 않음 —
--                   신규 행만 INSERT 된다.
--
-- 사용 예:
--   SELECT * FROM flopi_sync_link('remote_link', 'ds_tools', 'flopi_adm');
--   SELECT * FROM flopi_sync_link('remote_link', 'ds_tools', 'flopi_adm',
--                                 NULL, true);   -- 완전 미러(삭제 포함)
--   SELECT * FROM flopi_sync_link('remote_link', 'ds_tools', 'flopi_adm',
--                                 NULL, false, true);  -- 중복 스킵(신규만 INSERT)
--
-- 반환:
--   created       : 이번 호출에서 테이블을 생성했는지
--   affected_rows : INSERT + 실제 UPDATE 된 행 수
--   pruned_rows   : 삭제된 행 수 (p_prune=false 면 0)
--   sequences     : 보정된 시퀀스 {컬럼: 새 last_value} (없으면 {})
-- ============================================================================

-- 구버전(5개 파라미터) 시그니처 제거 — 오버로드 중복 호출 모호성 방지
DROP FUNCTION IF EXISTS flopi_sync_link(text, text, text, text[], boolean);

CREATE OR REPLACE FUNCTION flopi_sync_link(
    p_link_schema text,
    p_table       text,
    p_tgt_schema  text   DEFAULT current_schema(),
    p_pk_cols     text[] DEFAULT NULL,
    p_prune       boolean DEFAULT false,
    p_skip_dup    boolean DEFAULT false
) RETURNS TABLE (created boolean, affected_rows bigint,
                 pruned_rows bigint, sequences jsonb)
LANGUAGE plpgsql
AS $$
DECLARE
    v_created   boolean := false;
    v_pk_cols   text[];
    v_src_pk    text[];
    v_srv       text;
    v_rschema   text;
    v_rtable    text;
    v_conflict  text;
    v_col_list  text;
    v_upd_set   text;
    v_distinct  text;
    v_join      text;
    v_affected  bigint := 0;
    v_pruned    bigint := 0;
    v_col       text;
    v_seq       text;
    v_newval    bigint;
    v_seqs      jsonb := '{}'::jsonb;
BEGIN
    -- ── 0) 링크 테이블 존재 확인 ──────────────────────────────────────
    IF to_regclass(format('%I.%I', p_link_schema, p_table)) IS NULL THEN
        RAISE EXCEPTION '링크 테이블이 없습니다: %.%', p_link_schema, p_table;
    END IF;

    -- ── 1) 로컬 테이블 없으면 생성 (구조 복사 + PK 자동 탐지) ─────────
    IF to_regclass(format('%I.%I', p_tgt_schema, p_table)) IS NULL THEN
        v_src_pk := p_pk_cols;

        IF v_src_pk IS NULL THEN
            -- (a) 소스가 외부 테이블이면: 카탈로그에서 링크 서버명 + 원격
            --     스키마/테이블명을 알아내 dblink 로 원격 PK 조회
            SELECT fs.srvname,
                   COALESCE(o_schema.option_value, p_link_schema),
                   COALESCE(o_table.option_value,  p_table)
            INTO v_srv, v_rschema, v_rtable
            FROM pg_foreign_table ft
            JOIN pg_foreign_server fs ON fs.oid = ft.ftserver
            LEFT JOIN LATERAL (
                SELECT option_value FROM pg_options_to_table(ft.ftoptions)
                WHERE option_name = 'schema_name') o_schema ON true
            LEFT JOIN LATERAL (
                SELECT option_value FROM pg_options_to_table(ft.ftoptions)
                WHERE option_name = 'table_name') o_table ON true
            WHERE ft.ftrelid =
                  format('%I.%I', p_link_schema, p_table)::regclass;

            IF v_srv IS NOT NULL THEN
                BEGIN
                    SELECT t.pk INTO v_src_pk
                    FROM dblink(v_srv, format(
                        'SELECT array_agg(a.attname ORDER BY x.ord)
                         FROM pg_index i
                         JOIN LATERAL unnest(i.indkey) WITH ORDINALITY
                              AS x(attnum, ord) ON true
                         JOIN pg_attribute a
                           ON a.attrelid = i.indrelid AND a.attnum = x.attnum
                         WHERE i.indrelid = %L::regclass AND i.indisprimary',
                        format('%I.%I', v_rschema, v_rtable)))
                         AS t(pk text[]);
                EXCEPTION WHEN undefined_function THEN
                    RAISE EXCEPTION
                      '원격 PK 자동 탐지에는 dblink 확장이 필요합니다: '
                      'CREATE EXTENSION dblink; (또는 p_pk_cols 를 직접 지정)';
                END;
            ELSE
                -- (b) 소스가 로컬 테이블이면 소스 PK 그대로 사용
                SELECT array_agg(a.attname ORDER BY x.ord)
                INTO v_src_pk
                FROM pg_index i
                JOIN LATERAL unnest(i.indkey) WITH ORDINALITY
                     AS x(attnum, ord) ON true
                JOIN pg_attribute a
                  ON a.attrelid = i.indrelid AND a.attnum = x.attnum
                WHERE i.indrelid =
                      format('%I.%I', p_link_schema, p_table)::regclass
                  AND i.indisprimary;
            END IF;
        END IF;

        IF v_src_pk IS NULL OR array_length(v_src_pk, 1) IS NULL THEN
            RAISE EXCEPTION
              '원격 PK 를 찾지 못했습니다: %.% — p_pk_cols 로 직접 지정하세요',
              p_link_schema, p_table;
        END IF;

        EXECUTE format('CREATE TABLE %I.%I (LIKE %I.%I)',
                       p_tgt_schema, p_table, p_link_schema, p_table);
        EXECUTE format('ALTER TABLE %I.%I ADD PRIMARY KEY (%s)',
                       p_tgt_schema, p_table,
                       (SELECT string_agg(format('%I', c), ', ')
                        FROM unnest(v_src_pk) c));
        v_created := true;
        RAISE NOTICE '테이블 생성: %.% (PK: %)', p_tgt_schema, p_table, v_src_pk;
    END IF;

    -- ── 2) PK 컬럼 (로컬 카탈로그에서 확정) ───────────────────────────
    SELECT array_agg(a.attname ORDER BY x.ord)
    INTO v_pk_cols
    FROM pg_index i
    JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS x(attnum, ord) ON true
    JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = x.attnum
    WHERE i.indrelid = format('%I.%I', p_tgt_schema, p_table)::regclass
      AND i.indisprimary;

    IF v_pk_cols IS NULL THEN
        RAISE EXCEPTION 'PRIMARY KEY 가 없어 동기화할 수 없습니다: %.%',
              p_tgt_schema, p_table;
    END IF;

    v_conflict := (SELECT string_agg(format('%I', c), ', ')
                   FROM unnest(v_pk_cols) c);

    -- ── 3) 공통 컬럼 목록 (양쪽에 있는 컬럼만, 로컬 순서) ─────────────
    SELECT string_agg(format('%I', t.column_name), ', '
                      ORDER BY t.ordinal_position)
    INTO v_col_list
    FROM information_schema.columns t
    JOIN information_schema.columns s
      ON s.table_schema = p_link_schema AND s.table_name = p_table
     AND s.column_name  = t.column_name
    WHERE t.table_schema = p_tgt_schema AND t.table_name = p_table;

    -- ── 4) 정리(prune) — upsert 보다 먼저 (재키잉 UNIQUE 충돌 방지) ───
    IF p_prune THEN
        v_join := (SELECT string_agg(
                       format('s.%I = t.%I', c, c), ' AND ')
                   FROM unnest(v_pk_cols) c);
        EXECUTE format(
            'DELETE FROM %I.%I t WHERE NOT EXISTS
               (SELECT 1 FROM %I.%I s WHERE %s)',
            p_tgt_schema, p_table, p_link_schema, p_table, v_join);
        GET DIAGNOSTICS v_pruned = ROW_COUNT;
    END IF;

    -- ── 5) upsert: 없는 행 INSERT + 달라진 행만 UPDATE ───────────────
    SELECT string_agg(format('%I = EXCLUDED.%I', t.column_name, t.column_name),
                      ', ' ORDER BY t.ordinal_position),
           string_agg(format('%I.%I IS DISTINCT FROM EXCLUDED.%I',
                             p_table, t.column_name, t.column_name),
                      ' OR ' ORDER BY t.ordinal_position)
    INTO v_upd_set, v_distinct
    FROM information_schema.columns t
    JOIN information_schema.columns s
      ON s.table_schema = p_link_schema AND s.table_name = p_table
     AND s.column_name  = t.column_name
    WHERE t.table_schema = p_tgt_schema AND t.table_name = p_table
      AND t.column_name <> ALL (v_pk_cols);

    IF p_skip_dup THEN
        -- 중복 스킵 모드: 충돌 대상 미지정 DO NOTHING
        --   → PK 중복이든 보조 UNIQUE 중복이든 해당 행만 조용히 건너뛰고
        --     나머지 행은 계속 INSERT (에러로 중단되지 않음. UPDATE 도 안 함)
        EXECUTE format(
            'INSERT INTO %I.%I (%s) SELECT %s FROM %I.%I
             ON CONFLICT DO NOTHING',
            p_tgt_schema, p_table, v_col_list,
            v_col_list, p_link_schema, p_table);
    ELSIF v_upd_set IS NULL THEN
        EXECUTE format(
            'INSERT INTO %I.%I (%s) SELECT %s FROM %I.%I
             ON CONFLICT (%s) DO NOTHING',
            p_tgt_schema, p_table, v_col_list,
            v_col_list, p_link_schema, p_table, v_conflict);
    ELSE
        EXECUTE format(
            'INSERT INTO %I.%I (%s) SELECT %s FROM %I.%I
             ON CONFLICT (%s) DO UPDATE SET %s WHERE %s',
            p_tgt_schema, p_table, v_col_list,
            v_col_list, p_link_schema, p_table,
            v_conflict, v_upd_set, v_distinct);
    END IF;
    GET DIAGNOSTICS v_affected = ROW_COUNT;

    -- ── 6) PK 시퀀스 탐지 + 보정 ──────────────────────────────────────
    FOREACH v_col IN ARRAY v_pk_cols LOOP
        v_seq := pg_get_serial_sequence(
                     format('%I.%I', p_tgt_schema, p_table), v_col);
        IF v_seq IS NOT NULL THEN
            EXECUTE format(
                'SELECT setval(%L, (SELECT COALESCE(MAX(%I), 1) FROM %I.%I))',
                v_seq, v_col, p_tgt_schema, p_table)
            INTO v_newval;
            v_seqs := v_seqs || jsonb_build_object(v_col, v_newval);
        END IF;
    END LOOP;

    RETURN QUERY SELECT v_created, v_affected, v_pruned, v_seqs;
END;
$$;

COMMENT ON FUNCTION flopi_sync_link(text, text, text, text[], boolean, boolean) IS
'DB 링크(fdw) 테이블 완전 동기화: 없으면 CREATE(+원격 PK 자동 탐지), 있으면 PK 기준 upsert(변경분만 UPDATE), 옵션 prune(잉여행 삭제)·skip_dup(중복 스킵), PK 시퀀스 보정';
