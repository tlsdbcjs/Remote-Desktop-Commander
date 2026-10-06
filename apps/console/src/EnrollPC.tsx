import { useEffect, useRef, useState, type FormEvent } from "react";
import { z } from "zod";
import { api, type Session } from "./api";

const ticketSchema = z.object({
  token: z.string().min(20).max(128),
  expires_in_seconds: z.number().int().min(1).max(600),
  connection_file: z
    .object({
      version: z.literal(1),
      gateway: z.string(),
      token: z.string().min(20).max(128),
      expires_at: z.string(),
      ca_pem: z.string().nullable(),
    })
    .optional(),
});

export function EnrollPC({
  session,
  failure,
}: {
  session: Session;
  failure: (error: unknown) => void;
}) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [ticket, setTicket] = useState<{
    token: string;
    deadline: number;
    connection_file?: z.infer<typeof ticketSchema>["connection_file"];
  } | null>(null);
  const [remaining, setRemaining] = useState(0);
  const [notice, setNotice] = useState("");
  const active = useRef(true);
  const local = ["localhost", "127.0.0.1", "[::1]"].includes(
    window.location.hostname,
  );
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);
  useEffect(() => {
    if (!ticket) return;
    const timer = window.setInterval(() => {
      const seconds = Math.max(
        0,
        Math.ceil((ticket.deadline - Date.now()) / 1000),
      );
      setRemaining(seconds);
      if (seconds === 0) {
        setTicket(null);
        setNotice("등록 토큰이 만료됐습니다. 새 토큰을 발급하세요.");
      }
    }, 1000);
    return () => window.clearInterval(timer);
  }, [ticket]);

  async function issue(event: FormEvent) {
    event.preventDefault();
    if (busy || !name.trim()) return;
    setBusy(true);
    setTicket(null);
    setNotice("");
    try {
      const value = await api(
        "/enrollment-tokens",
        ticketSchema,
        session,
        "POST",
        { name: name.trim(), include_connection_file: true },
      );
      if (!active.current) return;
      setRemaining(value.expires_in_seconds);
      setTicket({
        token: value.token,
        deadline: Date.now() + value.expires_in_seconds * 1000,
        connection_file: value.connection_file,
      });
    } catch (error) {
      if (active.current) failure(error);
    } finally {
      if (active.current) setBusy(false);
    }
  }

  return (
    <section className="panel" aria-labelledby="enroll-pc-title">
      <h2 id="enroll-pc-title">새 PC 연결</h2>
      <p>
        이름을 정하고, 연결할 PC에서 Agent를 실행하세요. 허용할 폴더는 그 PC에서
        선택합니다.
      </p>
      {local && (
        <p>
          현재 주소는 같은 PC의 개발용 주소입니다. 다른 PC를 연결하려면
          Gateway의 HTTPS 주소로 Console을 여세요.
        </p>
      )}
      <form onSubmit={issue}>
        <label htmlFor="enrollment-name">PC 이름</label>
        <input
          id="enrollment-name"
          value={name}
          maxLength={128}
          required
          autoComplete="off"
          onChange={(event) => setName(event.target.value)}
        />
        <button disabled={busy || !name.trim()}>
          {busy ? "발급 중…" : "일회용 등록 토큰 만들기"}
        </button>
      </form>
      {ticket && (
        <div role="region" aria-label="PC 등록 안내">
          {ticket.connection_file && (
            <>
              <p>
                연결 파일을 다운로드해 원격 PC의 RACP Client에서 한 번
                선택하세요. 주소·토큰·CA를 따로 입력하지 않아도 됩니다.
              </p>
              <button
                onClick={() => {
                  const url = URL.createObjectURL(
                    new Blob(
                      [JSON.stringify(ticket.connection_file, null, 2)],
                      { type: "application/json" },
                    ),
                  );
                  const link = document.createElement("a");
                  link.href = url;
                  link.download = "RACP-connection.racp";
                  link.click();
                  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
                }}
              >
                연결 파일 다운로드
              </button>
              <p>
                파일에는 일회용 등록 토큰이 있습니다. 연결할 PC에만 전달하고,
                등록 뒤 삭제하세요. 허용 폴더와 권한은 해당 PC에서 선택합니다.
              </p>
            </>
          )}
          <p>
            연결할 PC에 RACP Agent를 설치한 후 다음 명령의 폴더 경로를 바꿔
            실행하세요.
          </p>
          <pre>{`racp-connect --gateway "${window.location.origin}" --workspace "허용할 폴더의 전체 경로"`}</pre>
          <p>
            토큰 입력창에 아래 값을 붙여 넣으세요. 기본 권한은 읽기 전용이며,
            토큰은 한 번만 사용합니다.
          </p>
          <label htmlFor="enrollment-token">일회용 등록 토큰</label>
          <textarea
            id="enrollment-token"
            readOnly
            value={ticket.token}
            autoComplete="off"
            spellCheck={false}
          />
          <p role="status">
            남은 시간 {remaining}초 · 등록 후 PC가 목록에 나타납니다. 연결된
            동안 Agent를 실행해 두세요.
          </p>
          <button
            className="quiet"
            onClick={() => {
              setTicket(null);
              setNotice("등록 안내를 닫았습니다.");
            }}
          >
            등록 안내 닫기
          </button>
        </div>
      )}
      {notice && <p role="status">{notice}</p>}
    </section>
  );
}
