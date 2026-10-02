import type { Route } from "next";
import { notFound, redirect } from "next/navigation";
import { StructureReviewPanel } from "../../../../review/StructureReviewPanel";
import { fetchStructureReview } from "../../../../review/server";
import { SignInRequired } from "../../../../review/session";

/** The structure review for one manuscript -- human gate #1. */
export default async function ReviewPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  let review;
  try {
    review = await fetchStructureReview(id);
  } catch (err) {
    if (err instanceof SignInRequired) {
      redirect(`/sign-in?next=${encodeURIComponent(`/manuscripts/${id}/review`)}` as Route);
    }
    throw err;
  }
  if (review === null) notFound();
  return <StructureReviewPanel review={review} />;
}
