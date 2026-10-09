import React from "react";
import {
  categoryEnabled,
  LocalPermissions,
  permissionCatalog,
  setCategory,
} from "./permissions";

export function PermissionsEditor({
  value,
  onChange,
  disabled,
  profile,
}: {
  value: LocalPermissions;
  onChange(value: LocalPermissions): void;
  disabled: boolean;
  profile: "read_only" | "standard" | "trusted_personal";
}) {
  const categories = [
    ...new Set(permissionCatalog.map((item) => item.category)),
  ];
  const broad = [
    "exec.argv",
    "exec.shell",
    "terminal.open",
    "browser.evaluate",
  ].some(
    (id) =>
      value.grants[id] === "allow" &&
      categoryEnabled(
        value,
        permissionCatalog.find((item) => item.id === id)!.category,
      ),
  );
  function constraint<K extends keyof LocalPermissions["constraints"]>(
    key: K,
    item: LocalPermissions["constraints"][K],
  ) {
    onChange({ ...value, constraints: { ...value.constraints, [key]: item } });
  }
  return (
    <div className="permission-editor">
      <h3>세부 권한</h3>
      <p className="hint">
        선택한 항목은 Agent가 실행·자료 전달 때 검사합니다. 설정은 다음 Agent
        시작부터 적용됩니다.
      </p>
      <p className="hint">
        {profile === "read_only"
          ? "읽기 전용 profile에서는 변경·실행 항목을 켜도 실행할 수 없습니다."
          : profile === "standard"
            ? "표준 profile의 변경·실행 작업에는 Gateway owner 승인이 추가로 필요합니다."
            : "개인용 profile에서도 여기에서 끈 권한은 차단됩니다."}
        실제 사용 가능 여부는 OS 권한·로그인 session·backend에 따라 실행 시
        확인합니다.
      </p>
      {broad && (
        <p className="permission-warning" role="note">
          프로그램·스크립트·브라우저 JavaScript는 현재 OS 계정으로 동작합니다.
          실행 파일 목록은 최초 실행 대상을 제한하며, 그 프로그램의
          파일·네트워크 접근이나 자식 실행을 OS 수준에서 격리하지 않습니다.
        </p>
      )}
      <div className="permission-categories">
        {categories.map((category) => {
          const items = permissionCatalog.filter(
            (item) => item.category === category,
          );
          const enabled = categoryEnabled(value, category);
          const available = items.filter(
            (item) => item.implementation === "rpc",
          );
          const selected = items.filter(
            (item) => value.grants[item.id] === "allow",
          ).length;
          return (
            <details
              key={category}
              open={category === "files_read" ? true : undefined}
            >
              <summary>
                <span>
                  {items[0].category_label}
                  <small>
                    {enabled ? selected : 0}개 허용 / {available.length}개 RPC
                    지원 · {items.length}개 항목
                    {selected > 0 && selected < available.length
                      ? " · 일부 선택"
                      : ""}
                  </small>
                </span>
              </summary>
              <label className="permission-category-switch">
                <input
                  type="checkbox"
                  role="switch"
                  aria-label={`${items[0].category_label} 사용`}
                  checked={enabled}
                  disabled={disabled || available.length === 0}
                  onChange={(event) =>
                    onChange(setCategory(value, category, event.target.checked))
                  }
                />
                카테고리 사용 · 끄면 세부 선택을 보존하고 모두 차단
              </label>
              {items.map((item) => (
                <div className="permission-leaf" key={item.id}>
                  <label>
                    <input
                      type="checkbox"
                      aria-label={`${item.label} (${item.id})`}
                      disabled={
                        disabled || !enabled || item.implementation !== "rpc"
                      }
                      checked={value.grants[item.id] === "allow"}
                      onChange={(event) =>
                        onChange({
                          ...value,
                          grants: {
                            ...value.grants,
                            [item.id]: event.target.checked ? "allow" : "deny",
                          },
                        })
                      }
                    />
                    <span>
                      <strong>{item.label}</strong>
                      <small>{item.description}</small>
                      <code>{item.id}</code>
                    </span>
                  </label>
                  <small className="permission-support">
                    {item.implementation === "rpc"
                      ? "구조화된 RPC 구현"
                      : item.implementation === "cli"
                        ? "내장 CLI · 전용 RPC 추가 예정"
                        : "추가 예정 · 사용 불가"}
                    {value.grants[item.id] === "require_approval" &&
                      " · 로컬 승인 미연결: 실행 차단"}
                  </small>
                </div>
              ))}
            </details>
          );
        })}
      </div>
      <details className="permission-constraints">
        <summary>대상·실행·출력 제약</summary>
        <label>
          작업 폴더 ID 제한 · 비우면 모든 허용 폴더
          <input
            disabled={disabled}
            value={value.constraints.workspace_ids.join(", ")}
            onChange={(event) =>
              constraint(
                "workspace_ids",
                event.target.value
                  .split(",")
                  .map((item) => item.trim())
                  .filter(Boolean),
              )
            }
          />
        </label>
        <label>
          요청 시간 상한 · 밀리초
          <input
            type="number"
            min={1}
            max={86400000}
            required
            disabled={disabled}
            value={value.constraints.max_timeout_ms}
            onChange={(event) =>
              constraint("max_timeout_ms", Number(event.target.value))
            }
          />
        </label>
        <label>
          출력·Artifact 크기 상한 · 바이트
          <input
            type="number"
            min={1}
            max={1073741824}
            required
            disabled={disabled}
            value={value.constraints.max_output_bytes}
            onChange={(event) =>
              constraint("max_output_bytes", Number(event.target.value))
            }
          />
        </label>
        {(["executable_allowlist", "executable_denylist"] as const).map(
          (key) => (
            <label key={key}>
              {key === "executable_allowlist"
                ? "실행 파일 허용 목록"
                : "실행 파일 차단 목록"}{" "}
              · 절대 경로를 한 줄에 하나씩
              <textarea
                disabled={disabled}
                value={value.constraints[key].join("\n")}
                onChange={(event) =>
                  constraint(
                    key,
                    event.target.value
                      .split("\n")
                      .filter((item) => item.trim()),
                  )
                }
              />
            </label>
          ),
        )}
        <p className="hint">
          차단 목록이 우선합니다. 목록을 설정하면 경로가 불명확한 명령과 shell
          문자열을 거부합니다. 자식 프로그램과 interpreter 동작을 격리하는
          sandbox는 현재 제공하지 않습니다.
        </p>
      </details>
    </div>
  );
}
