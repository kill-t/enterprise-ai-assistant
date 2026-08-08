/** 返回站内安全跳转地址，拒绝 //example.com 形式的协议相对外链。 */
export function resolveSafeRedirect(value: unknown): string {
  if (
    typeof value === "string" &&
    value.startsWith("/") &&
    !value.startsWith("//")
  ) {
    return value;
  }
  return "/";
}
