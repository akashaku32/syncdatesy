export interface MonthlyBillingData {
  cmp_id: number;
  current_month_labels: string[];
  current_month_billing: number[];
  billing_period: string;
}

export interface YearlyBillingData {
  years: number[];
  selected_year: number;
  monthly_labels: string[];
  monthly_total_rec_values: number[];
  cmp_id: string;
}

export interface BillingDetail {
  period?: string;
  total_usage?: number;
}

export interface InvoiceItem {
  id?: number;
  invoice_number?: string;
  created_at?: string;
  billing_start_date?: string;
  billing_end_date?: string;
  total_amount?: number;
  payment_status?: string;
  payment_reference?: string;
  payment_date?: string;
  receipt_url?: string;
}

export interface BillingDataMap {
  daily_labels?: string[];
  current_month_labels?: string[];
  labels?: string[];
  total_rec?: number[];
  current_month_billing?: number[];
  data?: number[];
  billing_details?: BillingDetail[];
  invoices?: InvoiceItem[];
  monthly_labels?: string[];
  monthly_total_rec_values?: number[];
}

export interface MonthlyUsageResponse extends BillingDataMap {
  selected_year?: number;
  selected_month?: number;
  selected_connection_ids?: number[];
  available_connections?: {
    connection_id: number;
    src_config__name: string;
    dst_config__name: string;
    source_type?: string;
  }[];
  amazon_s3_rec?: number[];
  salesforce_rec?: number[];
  dynamics_rec?: number[];
  google_reviews_rec?: number[];
  years?: number[];
  months?: [number, string][];
  connection_usage?: { connection_id: number; total_rec: number[] }[];
  connections_usage?: { connection_id: number; total_rec: number[] }[];
  connection_usage_data?: { connection_id: number; total_rec: number[] }[];
  connection_usage_map?: { connection_id: number; total_rec: number[] }[];
  connection_wise_usage?: { connection_id: number; total_rec: number[] }[];
}
