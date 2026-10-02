import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { deleteDataset, listDatasets, uploadDataset, type PageParams } from "@/services/api/resources";

export const datasetsKey = (params: PageParams = {}) => ["datasets", { page: params.page ?? 1, pageSize: params.pageSize ?? 20 }] as const;

export function useDatasets(params: PageParams = {}) {
  return useQuery({
    queryKey: datasetsKey(params),
    queryFn: () => listDatasets(params),
  });
}

export function useUploadDataset() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: uploadDataset,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["datasets"] }),
  });
}

export function useDeleteDataset() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteDataset,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["datasets"] }),
  });
}
