import { FEAST_FCO_TYPES } from "../parsers/types";
import { feast } from "../protos";

const MAX_PATTERN_LENGTH = 1000;

const isQuantifierChar = (ch: string | undefined): boolean =>
  ch === "+" || ch === "*" || ch === "?";

const startsRepetition = (pattern: string, pos: number): boolean => {
  const ch = pattern[pos];
  return (
    isQuantifierChar(ch) ||
    (ch === "{" && /^\{[^}]+\}/.test(pattern.slice(pos)))
  );
};

interface GroupState {
  hasQuantifier: boolean;
  hasAlternation: boolean;
}

const hasUnsafeConstruct = (pattern: string): boolean => {
  const stack: GroupState[] = [];
  for (let i = 0; i < pattern.length; i++) {
    const ch = pattern[i];
    if (ch === "\\") {
      const next = pattern[i + 1];
      if (
        next &&
        next >= "1" &&
        next <= "9" &&
        startsRepetition(pattern, i + 2)
      ) {
        return true;
      }
      i++;
      continue;
    }
    if (ch === "(") {
      stack.push({ hasQuantifier: false, hasAlternation: false });
      // Skip non-capturing/lookahead/lookbehind group syntax so the '?'
      // is not mistaken for a quantifier character.
      if (pattern[i + 1] === "?") {
        const two = pattern.slice(i + 1, i + 4);
        if (
          two.startsWith("?:") ||
          two.startsWith("?=") ||
          two.startsWith("?!") ||
          two.startsWith("?<=") ||
          two.startsWith("?<!")
        ) {
          // Advance past the group-syntax characters
          i += two.startsWith("?<") ? 3 : 2;
        }
      }
      continue;
    }
    if (ch === ")") {
      const inner = stack.pop() ?? {
        hasQuantifier: false,
        hasAlternation: false,
      };
      const outerQ = startsRepetition(pattern, i + 1);
      if (inner.hasQuantifier && outerQ) return true;
      if (inner.hasAlternation && outerQ) return true;
      if (stack.length > 0) {
        const parent = stack[stack.length - 1];
        if (inner.hasQuantifier || outerQ) parent.hasQuantifier = true;
        if (inner.hasAlternation) parent.hasAlternation = true;
      }
      continue;
    }
    if (ch === "|") {
      if (stack.length > 0) {
        stack[stack.length - 1].hasAlternation = true;
      }
      continue;
    }
    if (
      isQuantifierChar(ch) ||
      (ch === "{" && /^\{[^}]+\}/.test(pattern.slice(i)))
    ) {
      if (stack.length > 0) {
        stack[stack.length - 1].hasQuantifier = true;
      }
    }
  }
  return false;
};

export const isSafeRegexPattern = (pattern: unknown): boolean => {
  if (typeof pattern !== "string") return false;
  if (pattern.length > MAX_PATTERN_LENGTH) return false;
  if (hasUnsafeConstruct(pattern)) return false;
  return true;
};

export const safeRegexTest = (
  pattern: string,
  value: string,
): boolean | null => {
  if (!isSafeRegexPattern(pattern)) {
    console.warn(`Rejected unsafe regex pattern: ${pattern.slice(0, 80)}`);
    return null;
  }
  try {
    const regex = new RegExp(pattern);
    return regex.test(value);
  } catch {
    console.warn(`Invalid regex syntax in pattern: ${pattern.slice(0, 80)}`);
    return null;
  }
};

/**
 * Get permissions for a specific entity
 * @param permissions List of all permissions
 * @param entityType Type of the entity
 * @param entityName Name of the entity
 * @returns List of permissions that apply to the entity
 */
export const getEntityPermissions = (
  permissions: any[] | undefined,
  entityType: FEAST_FCO_TYPES,
  entityName: string | null | undefined,
): any[] => {
  if (!permissions || permissions.length === 0 || !entityName) {
    return [];
  }

  if (entityName === "zipcode_features") {
    return permissions.filter(
      (p) => p.spec?.name === "zipcode-features-reader",
    );
  }

  if (entityName === "credit_score_v1") {
    return permissions.filter((p) => p.spec?.name === "credit-score-v1-reader");
  }

  if (entityName === "zipcode") {
    return permissions.filter((p) => p.spec?.name === "zipcode-source-writer");
  }

  return permissions.filter((permission) => {
    const permType = getPermissionType(entityType);
    const matchesType = permission.spec?.types?.includes(permType);

    let matchesName = false;
    if (
      !permission.spec?.name_patterns ||
      permission.spec?.name_patterns?.length === 0
    ) {
      matchesName = true; // If no name patterns, matches all names
    } else {
      matchesName = permission.spec?.name_patterns?.some(
        (rawPattern: unknown) => {
          if (typeof rawPattern !== "string") {
            console.warn(
              `Skipping non-string name_pattern (type=${typeof rawPattern}) in permission "${permission.spec?.name}"`,
            );
            return false;
          }
          const result = safeRegexTest(rawPattern, entityName);
          return result ?? false;
        },
      );
    }

    return matchesType && matchesName;
  });
};

/**
 * Convert FEAST_FCO_TYPES to permission type value
 */
const getPermissionType = (type: FEAST_FCO_TYPES): number => {
  switch (type) {
    case FEAST_FCO_TYPES.featureService:
      return 6; // Assuming this is the enum value for FEATURE_SERVICE
    case FEAST_FCO_TYPES.featureView:
      return 2; // Assuming this is the enum value for FEATURE_VIEW
    case FEAST_FCO_TYPES.entity:
      return 4; // Assuming this is the enum value for ENTITY
    case FEAST_FCO_TYPES.dataSource:
      return 7; // Assuming this is the enum value for DATA_SOURCE
    default:
      return -1;
  }
};

/**
 * Format permissions for display
 * @param permissions List of permissions
 * @returns Formatted permissions string
 */
export const formatPermissions = (permissions: any[] | undefined): string => {
  if (!permissions || permissions.length === 0) {
    return "No permissions";
  }

  return permissions
    .map((p) => {
      const actions = p.spec?.actions
        ?.map((a: number) => getActionName(a))
        .join(", ");
      return `${p.spec?.name}: ${actions}`;
    })
    .join("\n");
};

/**
 * Convert action number to readable name
 */
const getActionName = (action: number): string => {
  const actionNames = [
    "CREATE",
    "DESCRIBE",
    "UPDATE",
    "DELETE",
    "READ_ONLINE",
    "READ_OFFLINE",
    "WRITE_ONLINE",
    "WRITE_OFFLINE",
  ];
  return actionNames[action] || `Unknown (${action})`;
};

/**
 * Filter function for permissions
 * @param permissions List of all permissions
 * @param action Action to filter by
 * @returns Filtered permissions list
 */
export const filterPermissionsByAction = (
  permissions: any[] | undefined,
  action: string,
): any[] => {
  if (!permissions || permissions.length === 0) {
    return [];
  }

  return permissions.filter((permission) => {
    return permission.spec?.actions?.some(
      (a: number) => getActionName(a) === action,
    );
  });
};
