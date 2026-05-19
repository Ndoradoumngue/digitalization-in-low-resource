import { useQuery } from "@tanstack/react-query";
import { fetchReviewCount, fetchReviewQueue, type ReviewQueueParams } from "@sdai/api-client";

export function useReviewQueue(params: ReviewQueueParams = {}) {
  return useQuery({
    queryKey: ["review-queue", params],
    queryFn:  () => fetchReviewQueue(params),
  });
}

export function useReviewCount() {
  return useQuery({
    queryKey:  ["review-count"],
    queryFn:   fetchReviewCount,
    staleTime: 30_000,
  });
}
