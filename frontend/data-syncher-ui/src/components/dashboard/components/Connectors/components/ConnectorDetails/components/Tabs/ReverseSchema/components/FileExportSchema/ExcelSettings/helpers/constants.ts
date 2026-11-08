import { type ExcelHeaderStyle, type ExcelOptions } from "@/types/connectors";

export const DEFAULT_HEADER_STYLE: ExcelHeaderStyle = {
  fill: "FFFFFF",
  font_color: "000000",
  bold: false,
  italic: false,
  font_name: "Calibri",
  font_size: 11,
  horizontal: "center",
  vertical: "center",
  wrap_text: true,
};

export const DEFAULT_SHEET_HEADER_STYLE: ExcelHeaderStyle = {
  fill: "2F5597",
  font_color: "FFFFFF",
  bold: true,
  italic: false,
  font_name: "Calibri",
  font_size: 16,
  horizontal: "center",
  vertical: "center",
};

export const DEFAULT_EXCEL_OPTIONS: ExcelOptions = {
  sheet_name: "Sheet1",
  auto_filter: true,
  freeze_header: true,
  auto_width: true,
  date_format: "yyyy-mm-dd",
  datetime_format: "yyyy-mm-dd hh:mm:ss",
  time_format: "hh:mm:ss",
  header_style: DEFAULT_HEADER_STYLE,
  sheet_header_style: DEFAULT_SHEET_HEADER_STYLE,
  sheet_header_enabled: false,
  hidden_columns: [],
};

export const STANDARD_FONTS = [
  "Calibri",
  "Arial",
  "Times New Roman",
  "Courier New",
  "Segoe UI",
  "Tahoma",
  "Verdana",
  "Georgia",
  "Garamond",
  "Trebuchet MS",
];

export const CONDITIONAL_RULE_TYPES = [
  { value: "cellis", label: "By Single Field" },
  { value: "multipleconditions", label: "By Multiple Fields" },
  { value: "containstext", label: "Text: Contains" },
  { value: "notcontainstext", label: "Text: Does Not Contain" },
  { value: "beginswith", label: "Text: Begins With" },
  { value: "endswith", label: "Text: Ends With" },
  { value: "timeperiod", label: "A Date Occurring" },
  { value: "top10", label: "Top / Bottom Ranked" },
  { value: "aboveaverage", label: "Above / Below Average" },
  { value: "duplicaterecords", label: "Duplicate Values" },
  { value: "uniquerecords", label: "Unique Values" },
  { value: "colorscale", label: "Color Scale (Gradient)" },
  { value: "databar", label: "Data Bar" },
  { value: "iconset", label: "Icon Set" },
  { value: "formula", label: "Custom Formula" },
];

export const TIME_PERIOD_OPTIONS = [
  { value: "today", label: "Today" },
  { value: "yesterday", label: "Yesterday" },
  { value: "tomorrow", label: "Tomorrow" },
  { value: "last7days", label: "In the last 7 days" },
  { value: "thisweek", label: "This week" },
  { value: "lastweek", label: "Last week" },
  { value: "nextweek", label: "Next week" },
  { value: "thismonth", label: "This month" },
  { value: "lastmonth", label: "Last month" },
  { value: "nextmonth", label: "Next month" },
];

export const ICON_SET_OPTIONS = [
  { value: "3TrafficLights1", label: "3 Traffic Lights (Unrimmed)" },
  { value: "3TrafficLights2", label: "3 Traffic Lights (Rimmed)" },
  { value: "3Arrows", label: "3 Arrows (Colored)" },
  { value: "3ArrowsGray", label: "3 Arrows (Gray)" },
  { value: "3Flags", label: "3 Flags" },
  { value: "3Symbols", label: "3 Symbols (Circled)" },
  { value: "3Symbols2", label: "3 Symbols (No Circle)" },
  { value: "4Arrows", label: "4 Arrows (Colored)" },
  { value: "5Arrows", label: "5 Arrows (Colored)" },
];
