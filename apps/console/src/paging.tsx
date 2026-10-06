import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { api, ApiError, listOf, type Session } from "./api";

export function useListPage<Schema extends z.ZodType>(
  resource: string,
  schema: Schema,
  session: Session | null,
  states: string[] = [],
  enabled = true,
) {
  const [cursor, setCursor] = useState<string | null>(null);
  const [history, setHistory] = useState<(string | null)[]>([]);
  const [filter, setFilter] = useState("");
  const [limit, setLimit] = useState(100);
  const [expired, setExpired] = useState(false);
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) params.set("cursor", cursor);
  if (filter) params.set(resource === "audit" ? "event" : "state", filter);
  const query = useQuery({
    queryKey: [resource, filter, limit, cursor],
    queryFn: () => api(`/${resource}?${params}`, listOf(schema), session),
    enabled: !!session && enabled,
    retry: (count, error) => !(error instanceof ApiError) && count < 1,
  });
  function reset() {
    setCursor(null);
    setHistory([]);
  }
  useEffect(() => {
    const refresh = () => {
      reset();
      setExpired(false);
    };
    window.addEventListener("racp-full-refresh", refresh);
    return () => window.removeEventListener("racp-full-refresh", refresh);
  }, []);
  useEffect(() => {
    if (
      cursor &&
      query.error instanceof ApiError &&
      query.error.code === "CURSOR_EXPIRED"
    ) {
      reset();
      setExpired(true);
    }
  }, [query.error, cursor]);
  const controls = (
    <div className="page-controls" aria-label={`${resource} 목록 탐색`}>
      {expired && (
        <p role="status">
          목록 cursor가 만료되어 첫 페이지를 다시 조회했습니다.
        </p>
      )}
      <label>
        페이지 크기
        <select
          aria-label={`${resource} 페이지 크기`}
          value={limit}
          onChange={(event) => {
            setLimit(Number(event.target.value));
            reset();
          }}
        >
          {[10, 25, 100, 500].map((size) => (
            <option key={size}>{size}</option>
          ))}
        </select>
      </label>
      {!!states.length && (
        <label>
          상태
          <select
            aria-label={`${resource} 상태 필터`}
            value={filter}
            onChange={(event) => {
              setFilter(event.target.value);
              reset();
            }}
          >
            <option value="">전체</option>
            {states.map((state) => (
              <option key={state}>{state}</option>
            ))}
          </select>
        </label>
      )}
      {resource === "audit" && (
        <label>
          이벤트
          <select
            aria-label="audit 이벤트 필터"
            value={filter}
            onChange={(event) => {
              setFilter(event.target.value);
              reset();
            }}
          >
            <option value="">전체</option>
            {[
              "state_changed",
              "approval_requested",
              "approval_decided",
              "device_status_changed",
              "artifact_ready",
              "console_session_created",
            ].map((value) => (
              <option key={value}>{value}</option>
            ))}
          </select>
        </label>
      )}
      <button
        className="quiet"
        disabled={!history.length || query.isFetching}
        onClick={() => {
          const previous = history.at(-1)!;
          setHistory(history.slice(0, -1));
          setCursor(previous);
        }}
      >
        이전
      </button>
      <span>페이지 {history.length + 1}</span>
      <button
        className="quiet"
        disabled={!query.data?.next_cursor || query.isFetching}
        onClick={() => {
          setHistory([...history, cursor]);
          setCursor(query.data!.next_cursor!);
          setExpired(false);
        }}
      >
        다음
      </button>
      <small>
        변경 중 항목 누락이 있을 수 있습니다. 최신 목록은 첫 페이지에서
        확인하세요.
      </small>
      <button
        className="quiet"
        onClick={() => {
          reset();
          void query.refetch();
        }}
      >
        처음부터 새로 조회
      </button>
    </div>
  );
  return { ...query, controls };
}
