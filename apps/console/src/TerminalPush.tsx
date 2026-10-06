import { useEffect, useRef, useState } from "react";
import { z } from "zod";
import { safeText } from "./api";

const offset = z.string().regex(/^\d{1,20}$/);
const frame = z.object({
  protocol: z.literal(1),
  type: z.enum(["stream_opened", "stream_data", "stream_gap", "stream_end"]),
  device_id: z.string(),
  handle_id: z.string(),
  stream_id: z.string(),
  agent_boot_id: z.string(),
  connection_epoch: z.number().int(),
  data: z.string().max(65536).optional(),
  byte_offset: offset.optional(),
  next_cursor: offset.optional(),
  earliest_cursor: offset.optional(),
  lost_bytes: offset.optional(),
  error: z.record(z.string(), z.unknown()).nullable().optional(),
});

export function TerminalPush({
  device,
  handle,
  cursor,
  chooseCursor,
}: {
  device: string;
  handle: string;
  cursor: string;
  chooseCursor: (value: string) => void;
}) {
  const socket = useRef<WebSocket | null>(null);
  const pendingAck = useRef<{ stream_id: string; byte_offset: string } | null>(
    null,
  );
  const pendingEnd = useRef(false);
  const [text, setText] = useState("");
  const [status, setStatus] = useState("구독하지 않음");
  const [nextCursor, setNextCursor] = useState(cursor);
  const [gap, setGap] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);
  useEffect(
    () => () => {
      socket.current?.close();
    },
    [device, handle],
  );
  useEffect(() => {
    const ack = pendingAck.current;
    if (ack && socket.current?.readyState === WebSocket.OPEN) {
      // This effect runs after React committed the displayed text.
      socket.current.send(JSON.stringify({ type: "stream_ack", ...ack }));
      pendingAck.current = null;
    }
    if (pendingEnd.current && !pendingAck.current) {
      pendingEnd.current = false;
      socket.current?.close();
    }
  });
  function stop() {
    socket.current?.close();
    socket.current = null;
    setConnected(false);
    setStatus("구독 중지 · terminal은 유지됨");
  }
  function start() {
    stop();
    pendingAck.current = null;
    pendingEnd.current = false;
    setGap(null);
    try {
      offset.parse(cursor);
    } catch {
      setStatus("cursor는 20자리 이하의 10진 byte offset이어야 합니다.");
      return;
    }
    setText("");
    setNextCursor(cursor);
    setStatus("연결 중…");
    const url = new URL(
      `/api/v1/devices/${encodeURIComponent(device)}/terminals/${encodeURIComponent(handle)}/stream`,
      location.href,
    );
    url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const connection = new WebSocket(url);
    socket.current = connection;
    let expected = cursor;
    let identity: { stream: string; boot: string; epoch: number } | null = null;
    connection.onopen = () =>
      connection.send(
        JSON.stringify({
          type: "stream_open",
          cursor,
          max_bytes: 65536,
          window_bytes: 262144,
        }),
      );
    connection.onmessage = (event) => {
      if (socket.current !== connection) return;
      try {
        const value: unknown = JSON.parse(String(event.data));
        const failure = z
          .object({
            type: z.literal("stream_error"),
            error: z.object({ code: z.string(), message: z.string() }),
          })
          .safeParse(value);
        if (failure.success) {
          setStatus(
            `${failure.data.error.code}: ${failure.data.error.message}`,
          );
          connection.close();
          return;
        }
        const message = frame.parse(value);
        if (message.device_id !== device || message.handle_id !== handle)
          throw new Error("stream 범위가 일치하지 않습니다.");
        if (message.type === "stream_opened") {
          identity = {
            stream: message.stream_id,
            boot: message.agent_boot_id,
            epoch: message.connection_epoch,
          };
          setConnected(true);
          setStatus("실시간 출력 수신 중");
          return;
        }
        if (
          !identity ||
          message.stream_id !== identity.stream ||
          message.agent_boot_id !== identity.boot ||
          message.connection_epoch !== identity.epoch
        )
          throw new Error("오래된 연결의 frame을 거부했습니다.");
        if (message.type === "stream_data") {
          if (
            message.byte_offset !== expected ||
            !message.next_cursor ||
            message.data === undefined
          )
            throw new Error("stream byte 순서가 일치하지 않습니다.");
          const size = BigInt(message.next_cursor) - BigInt(expected);
          if (size <= 0n || size > 65536n)
            throw new Error("stream chunk 한도를 초과했습니다.");
          expected = message.next_cursor;
          setText((previous) =>
            (previous + safeText(message.data!)).slice(-65536),
          );
          setNextCursor(expected);
          pendingAck.current = {
            stream_id: identity.stream,
            byte_offset: expected,
          };
        } else if (message.type === "stream_gap") {
          setStatus(
            `출력 유실 · ${message.lost_bytes} bytes. 시작 cursor를 직접 선택하세요.`,
          );
          setGap(message.earliest_cursor ?? null);
          connection.close();
        } else if (message.type === "stream_end") {
          setStatus(
            message.error
              ? `${String(message.error.code)}: ${String(message.error.message)}`
              : "출력 종료",
          );
          pendingEnd.current = true;
        }
      } catch (error) {
        setStatus(error instanceof Error ? error.message : "stream frame 오류");
        connection.close();
      }
    };
    connection.onerror = () => {
      if (socket.current === connection)
        setStatus("연결 실패 · 세션/Handle 상태를 조회하세요.");
    };
    connection.onclose = () => {
      if (socket.current === connection) {
        setConnected(false);
        socket.current = null;
      }
    };
  }
  return (
    <section aria-label="Terminal 실시간 viewer">
      <h3>실시간 출력</h3>
      <p role="status">{status}</p>
      <button disabled={!device || !handle || connected} onClick={start}>
        실시간 구독
      </button>
      <button className="quiet" disabled={!connected} onClick={stop}>
        구독 중지
      </button>
      <p>
        최근 65,536자만 표시 · 다음 byte cursor <code>{nextCursor}</code>
      </p>
      <button
        className="quiet"
        disabled={connected}
        onClick={() => chooseCursor(nextCursor)}
      >
        마지막 cursor 사용
      </button>
      {gap && (
        <button className="quiet" onClick={() => chooseCursor(gap)}>
          유실 이후 {gap}부터 읽기 선택
        </button>
      )}
      <pre aria-label="Terminal 실시간 출력">
        {text || "아직 수신한 출력이 없습니다."}
      </pre>
      <p>구독은 terminal 입력을 보내거나 idle TTL을 연장하지 않습니다.</p>
    </section>
  );
}
