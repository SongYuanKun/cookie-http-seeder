/** Labels partition storage; sharing a token still shares administrative access. */
export function senderTag(value = "default") {
  if (typeof value !== "string" || !/^[a-z0-9][a-z0-9_-]{0,31}$/.test(value) ||
      /^(con|prn|aux|nul|com[1-9]|lpt[1-9])$/.test(value)) {
    throw new Error("发送端标签需为 1–32 位小写字母、数字、横线或下划线，不能使用系统保留名");
  }
  return value;
}
