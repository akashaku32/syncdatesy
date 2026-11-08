export interface PaginationResponse<T> {
  content: T[];
  totalElements: number;
  size: number;
}
