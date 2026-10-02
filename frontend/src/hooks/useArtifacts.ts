import { useQuery } from "@tanstack/react-query";
import { getArtifact } from "@/services/api/resources";

export const artifactKey = (artifactId: string) => ["artifact", artifactId] as const;

export function useArtifact(artifactId: string | undefined) {
  return useQuery({
    queryKey: artifactKey(artifactId ?? ""),
    queryFn: () => getArtifact(artifactId as string),
    enabled: Boolean(artifactId),
  });
}
