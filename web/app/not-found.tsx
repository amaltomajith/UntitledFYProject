import Link from "next/link";
import { NOT_FOUND } from "@/lib/content";

export default function NotFound() {
  return (
    <>
      <div className="cone cone--quiet" aria-hidden="true" />
      <main className="lost container">
        <h1 className="display-3">{NOT_FOUND.title}</h1>
        <p className="lead">{NOT_FOUND.body}</p>
        <div className="lost__actions">
          <Link href="/ide" className="btn btn--primary">
            {NOT_FOUND.primary}
          </Link>
          <Link href="/" className="btn btn--ghost">
            {NOT_FOUND.secondary}
          </Link>
        </div>
      </main>
    </>
  );
}
