import {
  type ConditionRuleGroup,
  type ConditionRuleItem,
  type ConditionRuleNode,
  createGroup,
  createRule,
} from "./groupModelUtils";
import { getColumnDataType } from "./utils";

export interface ParseResult {
  tree: ConditionRuleGroup | null;
  error: string | null;
  unknownField?: string;
  fieldRange?: { start: number; end: number };
  suggestedColumns?: string[];
}

interface ParseStepResult {
  node: ConditionRuleNode | null;
  error: string | null;
  unknownField?: string;
  fieldRange?: { start: number; end: number };
  suggestedColumns?: string[];
}

interface Token {
  type:
    | "LPAREN"
    | "RPAREN"
    | "AND"
    | "OR"
    | "BETWEEN_AND"
    | "OPERATOR"
    | "IDENTIFIER"
    | "LITERAL";
  value: string;
  opCode?: string;
  start: number;
  end: number;
}

// Tokenizes the condition string into tokens
function tokenize(
  input: string,
  colNames: string[] = [],
): { tokens: Token[]; error: string | null } {
  const tokens: Token[] = [];
  let i = 0;
  const len = input.length;

  // Sort known column names by length descending so longer matching columns match first
  const sortedCols = [...colNames].sort((a, b) => b.length - a.length);

  const skipWhitespace = () => {
    while (i < len && /\s/.test(input[i])) {
      i++;
    }
  };

  while (i < len) {
    skipWhitespace();
    if (i >= len) break;

    const char = input[i];

    // Parentheses
    if (char === "(") {
      tokens.push({ type: "LPAREN", value: "(", start: i, end: i + 1 });
      i++;
      continue;
    }
    if (char === ")") {
      tokens.push({ type: "RPAREN", value: ")", start: i, end: i + 1 });
      i++;
      continue;
    }

    // Two-character operators: <=, >=, !=, <>
    const twoChar = input.slice(i, i + 2);
    if (twoChar === "<=") {
      tokens.push({
        type: "OPERATOR",
        value: "<=",
        opCode: "lessThanOrEqual",
        start: i,
        end: i + 2,
      });
      i += 2;
      continue;
    }
    if (twoChar === ">=") {
      tokens.push({
        type: "OPERATOR",
        value: ">=",
        opCode: "greaterThanOrEqual",
        start: i,
        end: i + 2,
      });
      i += 2;
      continue;
    }
    if (twoChar === "!=" || twoChar === "<>") {
      tokens.push({
        type: "OPERATOR",
        value: twoChar,
        opCode: "notEqual",
        start: i,
        end: i + 2,
      });
      i += 2;
      continue;
    }

    // Single-character operators: =, <, >
    if (char === "=") {
      tokens.push({
        type: "OPERATOR",
        value: "=",
        opCode: "equal",
        start: i,
        end: i + 1,
      });
      i++;
      continue;
    }
    if (char === "<") {
      tokens.push({
        type: "OPERATOR",
        value: "<",
        opCode: "lessThan",
        start: i,
        end: i + 1,
      });
      i++;
      continue;
    }
    if (char === ">") {
      tokens.push({
        type: "OPERATOR",
        value: ">",
        opCode: "greaterThan",
        start: i,
        end: i + 1,
      });
      i++;
      continue;
    }

    // Quoted string literals: "..." or '...'
    if (char === '"' || char === "'") {
      const quoteChar = char;
      const start = i;
      i++;
      let val = "";
      let closed = false;
      while (i < len) {
        if (input[i] === "\\" && i + 1 < len) {
          val += input[i + 1];
          i += 2;
        } else if (input[i] === quoteChar) {
          closed = true;
          i++;
          break;
        } else {
          val += input[i];
          i++;
        }
      }
      if (!closed) {
        return {
          tokens: [],
          error: `Unterminated quote starting at position ${start + 1}`,
        };
      }
      tokens.push({ type: "LITERAL", value: val, start, end: i });
      continue;
    }

    // Bracketed column names: [Column Name]
    if (char === "[" || char === "`") {
      const closeChar = char === "[" ? "]" : "`";
      const start = i;
      i++;
      let col = "";
      let closed = false;
      while (i < len) {
        if (input[i] === closeChar) {
          closed = true;
          i++;
          break;
        }
        col += input[i];
        i++;
      }
      if (!closed) {
        return {
          tokens: [],
          error: `Unterminated column identifier starting at position ${start + 1}`,
        };
      }
      tokens.push({ type: "IDENTIFIER", value: col, start, end: i });
      continue;
    }

    // Greedy match against known column names first
    let matchedKnownCol = false;
    for (const col of sortedCols) {
      if (input.slice(i, i + col.length).toLowerCase() === col.toLowerCase()) {
        // Ensure word boundary or delimiter after column name
        const nextChar = input[i + col.length];
        if (!nextChar || /[\s()=!<>'"`,]/.test(nextChar)) {
          tokens.push({
            type: "IDENTIFIER",
            value: col,
            start: i,
            end: i + col.length,
          });
          i += col.length;
          matchedKnownCol = true;
          break;
        }
      }
    }
    if (matchedKnownCol) continue;

    // Multi-word phrase operators (case-insensitive)
    const rest = input.slice(i);
    const phraseOps: Array<{ pattern: RegExp; opCode: string; val: string }> = [
      {
        pattern: /^is\s+not\s+empty\b/i,
        opCode: "isNotEmpty",
        val: "is not empty",
      },
      {
        pattern: /^is\s+not\s+null\b/i,
        opCode: "isNotEmpty",
        val: "is not empty",
      },
      {
        pattern: /^is\s+not\s+blank\b/i,
        opCode: "isNotEmpty",
        val: "is not empty",
      },
      { pattern: /^is\s+empty\b/i, opCode: "isEmpty", val: "is empty" },
      { pattern: /^is\s+null\b/i, opCode: "isEmpty", val: "is empty" },
      { pattern: /^is\s+blank\b/i, opCode: "isEmpty", val: "is empty" },
      {
        pattern: /^does\s+not\s+contain\b/i,
        opCode: "notContains",
        val: "does not contain",
      },
      {
        pattern: /^not\s+contains\b/i,
        opCode: "notContains",
        val: "does not contain",
      },
      {
        pattern: /^does\s+not\s+start\s+with\b/i,
        opCode: "notBeginsWith",
        val: "does not start with",
      },
      {
        pattern: /^not\s+starts\s+with\b/i,
        opCode: "notBeginsWith",
        val: "does not start with",
      },
      {
        pattern: /^starts\s+with\b/i,
        opCode: "beginsWith",
        val: "starts with",
      },
      {
        pattern: /^begins\s+with\b/i,
        opCode: "beginsWith",
        val: "starts with",
      },
      {
        pattern: /^does\s+not\s+end\s+with\b/i,
        opCode: "notEndsWith",
        val: "does not end with",
      },
      {
        pattern: /^not\s+ends\s+with\b/i,
        opCode: "notEndsWith",
        val: "does not end with",
      },
      { pattern: /^ends\s+with\b/i, opCode: "endsWith", val: "ends with" },
      {
        pattern: /^not\s+between\b/i,
        opCode: "notBetween",
        val: "not between",
      },
      { pattern: /^between\b/i, opCode: "between", val: "between" },
      { pattern: /^contains\b/i, opCode: "contains", val: "contains" },
    ];

    let matchedPhrase = false;
    for (const p of phraseOps) {
      const match = rest.match(p.pattern);
      if (match) {
        tokens.push({
          type: "OPERATOR",
          value: match[0],
          opCode: p.opCode,
          start: i,
          end: i + match[0].length,
        });
        i += match[0].length;
        matchedPhrase = true;
        break;
      }
    }
    if (matchedPhrase) continue;

    // Check for logical keywords AND, OR
    const keywordMatch = rest.match(/^(AND|OR)\b/i);
    if (keywordMatch) {
      const kw = keywordMatch[1].toUpperCase();
      // Check if previous token was between or part of a between clause
      const lastToken = tokens[tokens.length - 1];
      const isBetweenContext =
        kw === "AND" &&
        tokens.some((t, idx) => {
          if (
            t.type === "OPERATOR" &&
            (t.opCode === "between" || t.opCode === "notBetween")
          ) {
            // Check if there hasn't been a closing BETWEEN_AND yet
            const hasClosed = tokens
              .slice(idx)
              .some((subT) => subT.type === "BETWEEN_AND");
            return !hasClosed;
          }
          return false;
        });

      if (
        isBetweenContext &&
        lastToken &&
        (lastToken.type === "LITERAL" || lastToken.type === "IDENTIFIER")
      ) {
        tokens.push({
          type: "BETWEEN_AND",
          value: "AND",
          start: i,
          end: i + keywordMatch[0].length,
        });
      } else {
        tokens.push({
          type: kw as "AND" | "OR",
          value: kw,
          start: i,
          end: i + keywordMatch[0].length,
        });
      }
      i += keywordMatch[0].length;
      continue;
    }

    // Bare word / number / identifier
    const wordMatch = rest.match(/^[a-zA-Z0-9_$.%-]+/);
    if (wordMatch) {
      const word = wordMatch[0];
      const start = i;
      i += word.length;
      // If it's a pure number or date-like, treat as literal
      const isNum =
        !isNaN(Number(word)) && !word.startsWith("$") && !word.startsWith("_");
      const isDate = /^\d{4}-\d{2}-\d{2}$/.test(word);
      const isBool =
        word.toUpperCase() === "TRUE" || word.toUpperCase() === "FALSE";

      if (isNum || isDate || isBool) {
        tokens.push({ type: "LITERAL", value: word, start, end: i });
      } else {
        tokens.push({ type: "IDENTIFIER", value: word, start, end: i });
      }
      continue;
    }

    // Unexpected character
    return {
      tokens: [],
      error: `Unexpected character '${char}' at position ${i + 1}`,
    };
  }

  return { tokens, error: null };
}

/**
 * Parses human-readable condition text into a ConditionRuleGroup tree with strict validation.
 */
export function parseHumanCondition(
  rawText: string,
  colNames: string[] = [],
  tableFields?: Record<string, string>,
): ParseResult {
  const text = rawText.trim();
  if (!text) {
    return {
      tree: { id: "root", type: "group", conjunction: "and", children: [] },
      error: null,
    };
  }

  // 1. Tokenize
  const { tokens, error: tokenError } = tokenize(text, colNames);
  if (tokenError) {
    return { tree: null, error: tokenError };
  }

  if (tokens.length === 0) {
    return {
      tree: { id: "root", type: "group", conjunction: "and", children: [] },
      error: null,
    };
  }

  // 2. Parser state
  let pos = 0;
  let ruleCounter = 1;
  let groupCounter = 1;

  const peek = (): Token | undefined => tokens[pos];
  const next = (): Token | undefined => tokens[pos++];

  // Helper to validate column name
  const resolveColumn = (name: string): string => {
    if (!colNames || colNames.length === 0) return name;
    const exact = colNames.find((c) => c === name);
    if (exact) return exact;
    const caseMatch = colNames.find(
      (c) => c.toLowerCase() === name.toLowerCase(),
    );
    if (caseMatch) return caseMatch;
    return "";
  };

  // Parser: OrExpression -> AndExpression ('OR' AndExpression)*
  const parseOrExpression = (): ParseStepResult => {
    const first = parseAndExpression();
    if (first.error || !first.node) return first;

    const children: ConditionRuleNode[] = [first.node];

    while (peek() && peek()!.type === "OR") {
      next(); // consume OR
      if (!peek()) {
        return { node: null, error: "Unexpected end of condition after 'OR'" };
      }
      const right = parseAndExpression();
      if (right.error || !right.node) return right;
      children.push(right.node);
    }

    if (children.length === 1) {
      return { node: children[0], error: null };
    }

    const group = createGroup(`grp_${groupCounter++}`, "or", children);
    return { node: group, error: null };
  };

  // Parser: AndExpression -> Primary ('AND' Primary)*
  const parseAndExpression = (): ParseStepResult => {
    const first = parsePrimary();
    if (first.error || !first.node) return first;

    const children: ConditionRuleNode[] = [first.node];

    while (peek() && peek()!.type === "AND") {
      next(); // consume AND
      if (!peek()) {
        return { node: null, error: "Unexpected end of condition after 'AND'" };
      }
      const right = parsePrimary();
      if (right.error || !right.node) return right;
      children.push(right.node);
    }

    if (children.length === 1) {
      return { node: children[0], error: null };
    }

    const group = createGroup(`grp_${groupCounter++}`, "and", children);
    return { node: group, error: null };
  };

  // Parser: Primary -> '(' OrExpression ')' | ConditionClause
  const parsePrimary = (): ParseStepResult => {
    const token = peek();
    if (!token) {
      return { node: null, error: "Unexpected end of condition" };
    }

    if (token.type === "LPAREN") {
      next(); // consume '('
      const expr = parseOrExpression();
      if (expr.error || !expr.node) return expr;

      const closing = peek();
      if (!closing || closing.type !== "RPAREN") {
        return {
          node: null,
          error: "Mismatched parentheses: expected ')' to match '('",
        };
      }
      next(); // consume ')'

      // If the parenthesized node is a rule, wrap in a group so grouping hierarchy is preserved
      if (expr.node.type === "rule") {
        return {
          node: createGroup(`grp_${groupCounter++}`, "and", [expr.node]),
          error: null,
        };
      }
      return expr;
    }

    return parseConditionClause();
  };

  // Parser: ConditionClause -> Column Operator Value? ('AND' Value)?
  const parseConditionClause = (): ParseStepResult => {
    const colToken = peek();
    if (!colToken) {
      return { node: null, error: "Expected column name" };
    }

    if (colToken.type === "AND" || colToken.type === "OR") {
      return {
        node: null,
        error: `Unexpected '${colToken.value}' at this position`,
      };
    }

    if (colToken.type === "RPAREN") {
      return {
        node: null,
        error: "Unexpected closing parenthesis ')'",
      };
    }

    next(); // consume column token

    const rawCol = colToken.value;
    const resolvedCol = resolveColumn(rawCol);
    if (!resolvedCol && colNames && colNames.length > 0) {
      const search = rawCol.toLowerCase();
      const suggestions = colNames
        .filter((c) => c.toLowerCase().includes(search))
        .sort((a, b) => {
          const aStarts = a.toLowerCase().startsWith(search);
          const bStarts = b.toLowerCase().startsWith(search);
          if (aStarts && !bStarts) return -1;
          if (!aStarts && bStarts) return 1;
          return a.localeCompare(b);
        });

      return {
        node: null,
        error:
          suggestions.length > 0
            ? `Unknown field '${rawCol}'. Did you mean one of the matching columns below?`
            : `Unknown field '${rawCol}'`,
        unknownField: rawCol,
        fieldRange: { start: colToken.start, end: colToken.end },
        suggestedColumns: suggestions.slice(0, 8),
      };
    }
    const finalCol = resolvedCol || rawCol;

    const opToken = peek();
    if (!opToken || opToken.type !== "OPERATOR") {
      return {
        node: null,
        error: `Expected comparison operator after '${rawCol}', got '${opToken ? opToken.value : "end of input"}'`,
      };
    }

    next(); // consume operator
    const operator = opToken.opCode || "equal";

    // Operators without value: isEmpty, isNotEmpty
    if (operator === "isEmpty" || operator === "isNotEmpty") {
      const rule = createRule(`r_${ruleCounter++}`, finalCol, operator, "");
      return { node: rule, error: null };
    }

    // Between operator: needs Value BETWEEN_AND Value
    if (operator === "between" || operator === "notBetween") {
      const val1Token = peek();
      if (
        !val1Token ||
        (val1Token.type !== "LITERAL" && val1Token.type !== "IDENTIFIER")
      ) {
        return {
          node: null,
          error: `Missing minimum value for between condition on '${finalCol}'`,
        };
      }
      next(); // consume val1
      const val1 = val1Token.value;

      const andToken = peek();
      if (!andToken || andToken.type !== "BETWEEN_AND") {
        return {
          node: null,
          error: `Expected 'AND' after minimum value in between condition for '${finalCol}'`,
        };
      }
      next(); // consume 'AND'

      const val2Token = peek();
      if (
        !val2Token ||
        (val2Token.type !== "LITERAL" && val2Token.type !== "IDENTIFIER")
      ) {
        return {
          node: null,
          error: `Missing maximum value after 'AND' for between condition on '${finalCol}'`,
        };
      }
      next(); // consume val2
      const val2 = val2Token.value;

      // Validate numeric types if column is numeric
      const rawType = tableFields?.[finalCol];
      const colDataType = getColumnDataType(rawType);
      if (colDataType === "numeric") {
        if (isNaN(Number(val1)) && !val1.startsWith("=")) {
          return {
            node: null,
            error: `Minimum value '${val1}' must be a number for numeric column '${finalCol}'`,
          };
        }
        if (isNaN(Number(val2)) && !val2.startsWith("=")) {
          return {
            node: null,
            error: `Maximum value '${val2}' must be a number for numeric column '${finalCol}'`,
          };
        }
      }

      const rule: ConditionRuleItem = {
        id: `r_${ruleCounter++}`,
        type: "rule",
        column: finalCol,
        operator,
        value: val1,
        value2: val2,
      };
      return { node: rule, error: null };
    }

    // Standard operator: needs one value
    const valToken = peek();
    if (
      !valToken ||
      (valToken.type !== "LITERAL" && valToken.type !== "IDENTIFIER")
    ) {
      return {
        node: null,
        error: `Missing value for condition on '${finalCol}'`,
      };
    }
    next(); // consume value
    const val = valToken.value;

    // If operator is equal/notEqual and value is empty string, null, or blank,
    // convert directly to 'isEmpty' / 'isNotEmpty' operator so the builder reflects 'Is empty'
    const trimmedVal = val.trim();
    if (
      trimmedVal === "" ||
      trimmedVal.toLowerCase() === "null" ||
      trimmedVal.toLowerCase() === "blank"
    ) {
      if (operator === "equal") {
        return {
          node: createRule(`r_${ruleCounter++}`, finalCol, "isEmpty", ""),
          error: null,
        };
      }
      if (operator === "notEqual") {
        return {
          node: createRule(`r_${ruleCounter++}`, finalCol, "isNotEmpty", ""),
          error: null,
        };
      }
      return {
        node: null,
        error: `Value cannot be empty for operator '${opToken.value}' on '${finalCol}'. Use 'is empty' instead.`,
      };
    }

    // Validate type compatibility if available
    const rawType = tableFields?.[finalCol];
    const colDataType = getColumnDataType(rawType);
    if (
      colDataType === "numeric" &&
      isNaN(Number(val)) &&
      !val.startsWith("=")
    ) {
      return {
        node: null,
        error: `Value '${val}' must be a number for numeric column '${finalCol}'`,
      };
    }

    const rule: ConditionRuleItem = {
      id: `r_${ruleCounter++}`,
      type: "rule",
      column: finalCol,
      operator,
      value: val,
    };
    return { node: rule, error: null };
  };

  // Execute parse
  const result = parseOrExpression();
  if (result.error || !result.node) {
    return {
      tree: null,
      error: result.error || "Parsing failed",
      unknownField: result.unknownField,
      fieldRange: result.fieldRange,
      suggestedColumns: result.suggestedColumns,
    };
  }

  // Ensure all tokens were consumed
  if (pos < tokens.length) {
    const trailing = tokens[pos];
    let unknownField: string | undefined;
    let fieldRange: { start: number; end: number } | undefined;
    let suggestedColumns: string[] | undefined;

    if (trailing.type === "IDENTIFIER" && colNames && colNames.length > 0) {
      const search = trailing.value.toLowerCase();
      const suggestions = colNames
        .filter((c) => c.toLowerCase().includes(search))
        .sort((a, b) => {
          const aStarts = a.toLowerCase().startsWith(search);
          const bStarts = b.toLowerCase().startsWith(search);
          if (aStarts && !bStarts) return -1;
          if (!aStarts && bStarts) return 1;
          return a.localeCompare(b);
        });
      unknownField = trailing.value;
      fieldRange = { start: trailing.start, end: trailing.end };
      suggestedColumns = suggestions.slice(0, 8);
    }

    return {
      tree: null,
      error: `Unexpected token '${trailing.value}' at position ${trailing.start + 1}`,
      unknownField,
      fieldRange,
      suggestedColumns,
    };
  }

  // Normalize root to always be a group
  let finalRoot: ConditionRuleGroup;
  if (result.node.type === "group") {
    finalRoot = { ...result.node, id: "root" };
  } else {
    finalRoot = {
      id: "root",
      type: "group",
      conjunction: "and",
      children: [result.node],
    };
  }

  return { tree: finalRoot, error: null };
}
