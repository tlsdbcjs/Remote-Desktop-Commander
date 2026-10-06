import { useEffect, useState } from "react";
import {
  artifactSchema,
  auditSchema,
  safeText,
  ApiError,
  type Session,
} from "./api";
import { useListPage } from "./paging";
import type { components } from "./generated";

type Artifact = components["schemas"]["ArtifactView"];

export function Artifacts({ session }: { session: Session }) {
  const page = useListPage("artifacts", artifactSchema, session, [
    "READY",
    "UPLOADING",
    "VERIFYING",
    "FAILED",
    "EXPIRED",
    "DELETED",
  ]);
  const [selected, setSelected] = useState<Artifact | null>(null);
  return (
    <section className="panel">
      <h2>파일</h2>
      {page.controls}
      {page.isPending ? (
        <p role="status">불러오는 중…</p>
      ) : page.error ? (
        <p role="alert">{page.error.message}</p>
      ) : !page.data?.items.length ? (
        <p className="empty">파일이 없습니다.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Artifact / Device</th>
                <th>상태 / 크기</th>
                <th>조회</th>
              </tr>
            </thead>
            <tbody>
              {page.data.items.map((item) => (
                <tr key={item.id}>
                  <td>
                    <code>{item.id}</code>
                    <small>{item.device_id}</small>
                    <small>{item.media_type}</small>
                  </td>
                  <td>
                    {item.state}
                    <small>{item.size_bytes.toLocaleString()} bytes</small>
                    <small>
                      만료 {new Date(item.expires_at).toLocaleString()}
                    </small>
                  </td>
                  <td>
                    {item.state === "READY" && (
                      <>
                        <a
                          href={`/api/v1/artifacts/${encodeURIComponent(item.id)}/content`}
                          download
                        >
                          원본 다운로드
                        </a>
                        <button
                          className="quiet"
                          onClick={() => setSelected(item)}
                        >
                          미리보기
                        </button>
                      </>
                    )}
                    <details>
                      <summary>무결성 / 실행 범위</summary>
                      <code>SHA-256 {item.sha256}</code>
                      <p>{item.operation_id}</p>
                    </details>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {selected && (
        <ArtifactPreview artifact={selected} close={() => setSelected(null)} />
      )}
    </section>
  );
}

function ArtifactPreview({
  artifact,
  close,
}: {
  artifact: Artifact;
  close: () => void;
}) {
  const [preview, setPreview] = useState<{
    text?: string;
    image?: string;
    partial?: boolean;
  }>({});
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const controller = new AbortController();
    let image: string | undefined;
    let active = true;
    async function load() {
      const raster = ["image/png", "image/jpeg"].includes(artifact.media_type);
      if (
        !raster &&
        !["text/plain", "application/json"].includes(artifact.media_type)
      )
        throw new Error(
          "이 형식은 preview를 지원하지 않습니다. 원본 attachment를 내려받으세요.",
        );
      if (raster && artifact.size_bytes > 20 * 1024 ** 2)
        throw new Error("이미지 preview는 20 MiB까지 지원합니다.");
      if (artifact.size_bytes === 0) {
        setPreview({ text: "빈 파일입니다." });
        return;
      }
      const response = await fetch(
        `/api/v1/artifacts/${encodeURIComponent(artifact.id)}/content`,
        {
          credentials: "same-origin",
          signal: controller.signal,
          headers: {
            "If-Range": `"${artifact.sha256}"`,
            ...(raster ? {} : { Range: "bytes=0-65535" }),
          },
        },
      );
      if (!response.ok) {
        const value = await response.json();
        throw new ApiError(
          response.status,
          value.error?.code ?? "TRANSFER_ERROR",
          value.error?.message ?? "preview 조회 실패",
        );
      }
      if (response.headers.get("etag") !== `"${artifact.sha256}"`)
        throw new Error("파일 ETag가 달라 preview를 표시하지 않습니다.");
      const maximum = raster ? 20 * 1024 ** 2 : 65536;
      const reader = response.body!.getReader();
      const chunks: Uint8Array[] = [];
      let size = 0;
      try {
        while (true) {
          const next = await reader.read();
          if (next.done) break;
          size += next.value.length;
          if (size > maximum)
            throw new Error("preview 응답이 크기 제한을 초과했습니다.");
          chunks.push(next.value);
        }
      } finally {
        await reader.cancel();
      }
      const bytes = new Uint8Array(size);
      let offset = 0;
      for (const chunk of chunks) {
        bytes.set(chunk, offset);
        offset += chunk.length;
      }
      if (size === artifact.size_bytes) {
        const digest = Array.from(
          new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
          (value) => value.toString(16).padStart(2, "0"),
        ).join("");
        if (digest !== artifact.sha256)
          throw new Error("파일 SHA-256이 달라 preview를 표시하지 않습니다.");
      }
      if (raster) {
        if (!active || controller.signal.aborted) return;
        const png = bytes.slice(0, 8).join(",") === "137,80,78,71,13,10,26,10";
        const jpeg = bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255;
        if (!(artifact.media_type === "image/png" ? png : jpeg))
          throw new Error("PNG/JPEG signature가 일치하지 않습니다.");
        image = URL.createObjectURL(
          new Blob([bytes], { type: artifact.media_type }),
        );
        if (active) setPreview({ image });
      } else if (active)
        setPreview({
          text: safeText(new TextDecoder().decode(bytes)),
          partial: size < artifact.size_bytes,
        });
    }
    setPreview({});
    setError("");
    setLoading(true);
    void load()
      .catch((reason) => {
        if (active && !controller.signal.aborted)
          setError(reason instanceof Error ? reason.message : "preview 실패");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
      controller.abort();
      if (image) URL.revokeObjectURL(image);
    };
  }, [artifact]);
  return (
    <section aria-label="Artifact 미리보기" className="artifact-preview">
      <div className="section-title">
        <h3>미리보기 · {artifact.id}</h3>
        <button className="quiet" onClick={close}>
          미리보기 닫기
        </button>
      </div>
      {loading ? (
        <p role="status">파일 검증 중…</p>
      ) : error ? (
        <p role="alert">{error}</p>
      ) : preview.image ? (
        <img
          src={preview.image}
          alt="검증된 Artifact 이미지"
          onError={() => setError("이미지를 해석할 수 없습니다.")}
        />
      ) : (
        <pre>{preview.text}</pre>
      )}
      {preview.partial && (
        <p>
          앞부분 64 KiB만 표시했습니다. 원본 전체는 attachment로 내려받으세요.
        </p>
      )}
      <p>텍스트는 실행되지 않으며, PNG/JPEG만 이미지로 표시합니다.</p>
    </section>
  );
}

export function Audit({ session }: { session: Session }) {
  const page = useListPage("audit", auditSchema, session);
  return (
    <section className="panel">
      <h2>감사 기록</h2>
      {page.controls}
      {page.isPending ? (
        <p role="status">불러오는 중…</p>
      ) : page.error ? (
        <p role="alert">{page.error.message}</p>
      ) : !page.data?.items.length ? (
        <p className="empty">감사 기록이 없습니다.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>관측 시각</th>
                <th>이벤트 / 실행</th>
                <th>범위 / 요약</th>
              </tr>
            </thead>
            <tbody>
              {page.data.items.map((item) => (
                <tr key={item.id}>
                  <td>{new Date(item.timestamp).toLocaleString()}</td>
                  <td>
                    {item.event}
                    <small>{item.operation}</small>
                    <small>{item.operation_id}</small>
                  </td>
                  <td>
                    <code>{item.device_id || item.owner_id}</code>
                    <pre>{safeText(JSON.stringify(item.summary, null, 2))}</pre>
                    <details>
                      <summary>상관 ID</summary>
                      <p>Trace {item.trace_id}</p>
                      <p>Request {item.request_id}</p>
                    </details>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
