import {
  isSafeRegexPattern,
  safeRegexTest,
  getEntityPermissions,
} from "./permissionUtils";
import { FEAST_FCO_TYPES } from "../parsers/types";

describe("isSafeRegexPattern", () => {
  it("accepts simple patterns", () => {
    expect(isSafeRegexPattern("^feature_.*$")).toBe(true);
    expect(isSafeRegexPattern("my-feature")).toBe(true);
    expect(isSafeRegexPattern(".*")).toBe(true);
  });

  it("rejects patterns with nested quantifiers", () => {
    expect(isSafeRegexPattern("(a+)+")).toBe(false);
    expect(isSafeRegexPattern("(a+)*")).toBe(false);
    expect(isSafeRegexPattern("(.*a){10}*")).toBe(false);
    expect(isSafeRegexPattern("((((a+)+)+)+)")).toBe(false);
  });

  it("rejects deeply wrapped nested quantifiers", () => {
    expect(isSafeRegexPattern("((a+))+")).toBe(false);
    expect(isSafeRegexPattern("(((a+)))+")).toBe(false);
    expect(isSafeRegexPattern("((a*))*")).toBe(false);
    expect(isSafeRegexPattern("((a{2,}))+")).toBe(false);
  });

  it("accepts safe grouped patterns without nested quantification", () => {
    expect(isSafeRegexPattern("(abc)+")).toBe(true);
    expect(isSafeRegexPattern("([a-z])+")).toBe(true);
    expect(isSafeRegexPattern("(a)(b)+")).toBe(true);
  });

  it("handles escaped characters in patterns", () => {
    expect(isSafeRegexPattern("\\(a+\\)+")).toBe(true);
    expect(isSafeRegexPattern("\\(\\)")).toBe(true);
  });

  it("rejects quantified groups with alternation", () => {
    expect(isSafeRegexPattern("(a|aa)+")).toBe(false);
    expect(isSafeRegexPattern("(a|ab)*")).toBe(false);
    expect(isSafeRegexPattern("(a|a)*")).toBe(false);
    expect(isSafeRegexPattern("(foo|foobar){2,}")).toBe(false);
  });

  it("rejects nested alternation patterns", () => {
    expect(isSafeRegexPattern("((a|a))+")).toBe(false);
    expect(isSafeRegexPattern("((a|ab))*")).toBe(false);
    expect(isSafeRegexPattern("(((a|b)))+")).toBe(false);
  });

  it("rejects alternation with overlapping branches under quantifier", () => {
    expect(isSafeRegexPattern("(x|xy)+")).toBe(false);
    expect(isSafeRegexPattern("(abc|abcd)*")).toBe(false);
    expect(isSafeRegexPattern("(a|b|ab)+")).toBe(false);
    expect(isSafeRegexPattern("(cat|car|c){2,5}")).toBe(false);
  });

  it("rejects deeply nested quantified alternation constructs", () => {
    expect(isSafeRegexPattern("(((a|b)+))+")).toBe(false);
    expect(isSafeRegexPattern("((a+|b+))+")).toBe(false);
    expect(isSafeRegexPattern("(((x|y)*)?)+")).toBe(false);
  });

  it("rejects alternation-based attacks with lazy quantifiers", () => {
    expect(isSafeRegexPattern("(a|ab)*?")).toBe(false);
    expect(isSafeRegexPattern("(a|a)+?")).toBe(false);
  });

  it("rejects alternation-based attacks with repetition ranges", () => {
    expect(isSafeRegexPattern("(a|ab){1,100}")).toBe(false);
    expect(isSafeRegexPattern("(a|a){0,}")).toBe(false);
  });

  it("rejects non-capturing groups with quantified alternation", () => {
    expect(isSafeRegexPattern("(?:a|ab)+")).toBe(false);
    expect(isSafeRegexPattern("(?:a|a)*")).toBe(false);
    expect(isSafeRegexPattern("(?:foo|foobar){2,}")).toBe(false);
  });

  it("rejects backreference-based ReDoS patterns", () => {
    expect(isSafeRegexPattern("(a+)\\1+")).toBe(false);
    expect(isSafeRegexPattern("(a+)\\1*")).toBe(false);
    expect(isSafeRegexPattern("(.*?)\\1{2,}")).toBe(false);
  });

  it("accepts safe backreferences without quantifiers", () => {
    expect(isSafeRegexPattern("(a+)\\1")).toBe(true);
    expect(isSafeRegexPattern("(foo)\\1")).toBe(true);
  });

  it("handles non-capturing group syntax without false positives", () => {
    // (?:simple)+ is safe: no alternation, just a quantified non-capturing group
    expect(isSafeRegexPattern("(?:simple)+")).toBe(true);
    // (?:foo|bar) is safe: alternation but no outer quantifier on the group
    expect(isSafeRegexPattern("(?:foo|bar)")).toBe(true);
    // (?:a|b)+ is unsafe: alternation inside a quantified group
    expect(isSafeRegexPattern("(?:a|b)+")).toBe(false);
    // (?=lookahead) is safe: lookahead, no quantifier concern
    expect(isSafeRegexPattern("(?=lookahead)")).toBe(true);
    // (?!negative) is safe: negative lookahead
    expect(isSafeRegexPattern("(?!negative)")).toBe(true);
    // (?<=lookbehind) is safe: lookbehind
    expect(isSafeRegexPattern("(?<=lookbehind)")).toBe(true);
    // (?<!neglookbehind) is safe: negative lookbehind
    expect(isSafeRegexPattern("(?<!neglookbehind)")).toBe(true);
  });

  it("accepts alternation without quantifier (no backtracking risk)", () => {
    expect(isSafeRegexPattern("(a|b)")).toBe(true);
    expect(isSafeRegexPattern("(foo|bar)")).toBe(true);
    expect(isSafeRegexPattern("^(abc|def)$")).toBe(true);
  });

  it("returns false for non-string input", () => {
    expect(isSafeRegexPattern(123 as unknown)).toBe(false);
    expect(isSafeRegexPattern(null as unknown)).toBe(false);
    expect(isSafeRegexPattern(undefined as unknown)).toBe(false);
  });

  it("rejects overly long patterns", () => {
    expect(isSafeRegexPattern("a".repeat(1001))).toBe(false);
  });

  it("accepts patterns at the length limit", () => {
    expect(isSafeRegexPattern("a".repeat(1000))).toBe(true);
  });
});

describe("safeRegexTest", () => {
  it("returns true for matching safe patterns", () => {
    expect(safeRegexTest("^feature_.*$", "feature_view_1")).toBe(true);
  });

  it("returns false for non-matching safe patterns", () => {
    expect(safeRegexTest("^feature_.*$", "entity_1")).toBe(false);
  });

  it("returns null for unsafe patterns", () => {
    expect(safeRegexTest("(a+)+", "aaa")).toBe(null);
  });

  it("returns null for invalid regex syntax", () => {
    expect(safeRegexTest("[invalid", "test")).toBe(null);
  });

  it("logs a warning for unsafe patterns", () => {
    const warnSpy = jest.spyOn(console, "warn").mockImplementation();
    safeRegexTest("(a+)+", "aaa");
    expect(warnSpy).toHaveBeenCalledWith(
      expect.stringContaining("Rejected unsafe regex pattern"),
    );
    warnSpy.mockRestore();
  });

  it("logs a warning for invalid regex syntax", () => {
    const warnSpy = jest.spyOn(console, "warn").mockImplementation();
    safeRegexTest("[invalid", "test");
    expect(warnSpy).toHaveBeenCalledWith(
      expect.stringContaining("Invalid regex syntax"),
    );
    warnSpy.mockRestore();
  });
});

describe("getEntityPermissions", () => {
  const makePermission = (
    name: string,
    types: number[],
    namePatterns: string[],
  ) => ({
    spec: {
      name,
      types,
      name_patterns: namePatterns,
      actions: [0],
    },
  });

  it("returns empty array when no permissions", () => {
    expect(
      getEntityPermissions([], FEAST_FCO_TYPES.featureView, "test"),
    ).toEqual([]);
  });

  it("returns empty array when entityName is null", () => {
    expect(
      getEntityPermissions(
        [makePermission("p1", [2], [".*"])],
        FEAST_FCO_TYPES.featureView,
        null,
      ),
    ).toEqual([]);
  });

  it("matches permissions with safe regex patterns", () => {
    const perms = [
      makePermission("reader", [2], ["^feature_.*$"]),
      makePermission("writer", [2], ["^entity_.*$"]),
    ];
    const result = getEntityPermissions(
      perms,
      FEAST_FCO_TYPES.featureView,
      "feature_view_1",
    );
    expect(result).toHaveLength(1);
    expect(result[0].spec.name).toBe("reader");
  });

  it("rejects unsafe patterns (fail-closed)", () => {
    const perms = [makePermission("bad", [2], ["(a+)+"])];
    const result = getEntityPermissions(
      perms,
      FEAST_FCO_TYPES.featureView,
      "(a+)+",
    );
    expect(result).toHaveLength(0);
  });

  it("rejects non-string name_patterns entries", () => {
    const perms = [
      {
        spec: {
          name: "bad-type",
          types: [2],
          name_patterns: [123, null],
          actions: [0],
        },
      },
    ];
    const result = getEntityPermissions(
      perms,
      FEAST_FCO_TYPES.featureView,
      "anything",
    );
    expect(result).toHaveLength(0);
  });

  it("matches all names when no name_patterns specified", () => {
    const perms = [makePermission("all", [2], [])];
    const result = getEntityPermissions(
      perms,
      FEAST_FCO_TYPES.featureView,
      "anything",
    );
    expect(result).toHaveLength(1);
  });

  it("logs a warning for non-string name_patterns entries", () => {
    const warnSpy = jest.spyOn(console, "warn").mockImplementation();
    const perms = [
      {
        spec: {
          name: "bad-type-perm",
          types: [2],
          name_patterns: [123, null],
          actions: [0],
        },
      },
    ];
    getEntityPermissions(perms, FEAST_FCO_TYPES.featureView, "anything");
    expect(warnSpy).toHaveBeenCalledWith(
      expect.stringContaining("Skipping non-string name_pattern"),
    );
    expect(warnSpy).toHaveBeenCalledWith(
      expect.stringContaining("bad-type-perm"),
    );
    warnSpy.mockRestore();
  });

  it("logs a warning when unsafe patterns are rejected (fail-closed)", () => {
    const warnSpy = jest.spyOn(console, "warn").mockImplementation();
    const perms = [makePermission("evil", [2], ["(a+)+"])];
    getEntityPermissions(perms, FEAST_FCO_TYPES.featureView, "test");
    expect(warnSpy).toHaveBeenCalledWith(
      expect.stringContaining("Rejected unsafe regex pattern"),
    );
    warnSpy.mockRestore();
  });

  it("logs a warning when regex syntax is invalid", () => {
    const warnSpy = jest.spyOn(console, "warn").mockImplementation();
    const perms = [makePermission("broken", [2], ["[invalid"])];
    getEntityPermissions(perms, FEAST_FCO_TYPES.featureView, "test");
    expect(warnSpy).toHaveBeenCalledWith(
      expect.stringContaining("Invalid regex syntax"),
    );
    warnSpy.mockRestore();
  });

  it("rejects alternation-based ReDoS patterns in name_patterns (fail-closed)", () => {
    const perms = [makePermission("redos", [2], ["(a|ab)*"])];
    const result = getEntityPermissions(
      perms,
      FEAST_FCO_TYPES.featureView,
      "aaaaab",
    );
    expect(result).toHaveLength(0);
  });
});
