import { useMemo, useState } from "react";

interface UsePaginationProps<T> {
  data: T[];
  itemsPerPage?: number;
}

export const usePagination = <T>({
  data,
  itemsPerPage,
}: UsePaginationProps<T>) => {
  const [currentPage, setCurrentPage] = useState(1);

  const totalPages =
    itemsPerPage && itemsPerPage > 0
      ? Math.ceil(data.length / itemsPerPage)
      : 1;

  const currentData = useMemo(() => {
    if (!itemsPerPage || itemsPerPage <= 0) return data;
    const begin = (currentPage - 1) * itemsPerPage;
    const end = begin + itemsPerPage;
    return data.slice(begin, end);
  }, [data, currentPage, itemsPerPage]);

  const jumpToPage = (page: number) => {
    const pageNumber = Math.max(1, Math.min(page, totalPages));
    setCurrentPage(pageNumber);
  };

  return {
    currentPage,
    totalPages,
    currentData,
    jumpToPage,
    setCurrentPage,
  };
};
