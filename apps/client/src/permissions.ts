import catalog from "../../../packages/protocol/src/racp_protocol/permission_catalog.json";

export type Grant = "allow" | "deny" | "require_approval";
export type LocalPermissions = {
  version: 1;
  grants: Record<string, Grant>;
  disabled_categories: string[];
  constraints: {
    workspace_ids: string[];
    max_timeout_ms: number;
    max_output_bytes: number;
    executable_allowlist: string[];
    executable_denylist: string[];
    strict_os_isolation: false;
  };
};
export const permissionCatalog = catalog;
export function defaultPermissions(): LocalPermissions {
  return {
    version: 1,
    grants: Object.fromEntries(
      [
        "system.identity.read",
        "files.list",
        "files.search.names",
        "files.read.text",
        "files.metadata.read",
        "files.hash",
        "artifacts.export",
      ].map((id) => [id, "allow" as Grant]),
    ),
    disabled_categories: ["desktop_read", "desktop_input"],
    constraints: {
      workspace_ids: [],
      max_timeout_ms: 86400000,
      max_output_bytes: 1073741824,
      executable_allowlist: [],
      executable_denylist: [],
      strict_os_isolation: false,
    },
  };
}
export function categoryEnabled(
  value: LocalPermissions,
  category: string,
): boolean {
  return !value.disabled_categories.includes(category);
}
export function setCategory(
  value: LocalPermissions,
  category: string,
  enabled: boolean,
): LocalPermissions {
  return {
    ...value,
    disabled_categories: enabled
      ? value.disabled_categories.filter((item) => item !== category)
      : [...new Set([...value.disabled_categories, category])],
  };
}
export function desktopEnabled(value: LocalPermissions): boolean {
  return permissionCatalog.some(
    (item) =>
      item.id.startsWith("desktop.") &&
      item.implementation === "rpc" &&
      categoryEnabled(value, item.category) &&
      value.grants[item.id] === "allow",
  );
}
export function setDesktop(
  value: LocalPermissions,
  enabled: boolean,
): LocalPermissions {
  let updated = setCategory(
    setCategory(value, "desktop_read", enabled),
    "desktop_input",
    enabled,
  );
  if (enabled)
    updated = {
      ...updated,
      grants: {
        ...updated.grants,
        ...Object.fromEntries(
          permissionCatalog
            .filter(
              (item) =>
                item.id.startsWith("desktop.") && item.implementation === "rpc",
            )
            .map((item) => [item.id, "allow" as Grant]),
        ),
      },
    };
  return updated;
}
