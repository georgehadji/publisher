import { notFound } from "next/navigation";
import { StructureReviewPanel } from "../../../../review/StructureReviewPanel";
import { fetchStructureReview } from "../../../../review/server";

/** The structure review for one manuscript -- human gate #1. */
export default async function ReviewPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const review = await fetchStructureReview(id);
  if (review === null) notFound();
  return <StructureReviewPanel review={review} />;
}
