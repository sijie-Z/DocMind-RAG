// 最小 Node 内置模块声明（避免引入 @types/node；SDK 运行时本身不依赖 Node）。
declare module "node:test" {
  export function test(name: string, fn: () => void | Promise<void>): void;
}

declare module "node:assert/strict" {
  interface Assert {
    equal(actual: unknown, expected: unknown, message?: string): void;
    deepEqual(actual: unknown, expected: unknown, message?: string): void;
    ok(value: unknown, message?: string): void;
    rejects(
      fn: () => Promise<unknown>,
      validator?: (error: unknown) => boolean,
    ): Promise<void>;
  }
  const assert: Assert;
  export default assert;
}
