import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

export function FileHelp() {
  return (
    <Popover>
      <PopoverTrigger
        render={
          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            aria-label="Where to find files"
            title="Where to find files"
          >
            ?
          </Button>
        }
      />
      <PopoverContent className="w-[22rem] text-sm" side="top">
        <div>
          <div className="mb-0.5 text-xs font-semibold tracking-wide text-primary uppercase">MIDI scores (.mid)</div>
          Free MIDI for most classical pieces from the{" "}
          <a
            href="https://www.mutopiaproject.org/"
            target="_blank"
            rel="noreferrer"
            className="underline underline-offset-2 hover:text-primary"
          >
            Mutopia Project
          </a>{" "}
          (each piece's page lists its license; Public Domain and CC BY are safe to use), or the music21 corpus. Upload
          the .mid, then press Perform.
        </div>
        <div>
          <div className="mb-0.5 text-xs font-semibold tracking-wide text-primary uppercase">
            Documents for NDI directions
          </div>
          A libretto, a synopsis or program notes as PDF, Word or text, or any Wikipedia article saved as PDF. NDI reads
          it in about 15-30 s and gives every musician a one-line direction per section.
        </div>
        <div className="text-xs text-muted-foreground">Demo files: demo/midi/ in the repo.</div>
      </PopoverContent>
    </Popover>
  );
}
