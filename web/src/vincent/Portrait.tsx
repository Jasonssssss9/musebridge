import { useEffect, useRef, useState } from 'react';

const reducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;

/** A silent greeting runs at most once per page entry, independently of welcome audio. */
export function VincentPortrait({ allowGreeting, portraitUrl = '/vincent/listening.webp' }: {
  allowGreeting: boolean;
  portraitUrl?: string;
}) {
  const [finished, setFinished] = useState(() => !allowGreeting || reducedMotion());
  const [portraitFailed, setPortraitFailed] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => setPortraitFailed(false), [portraitUrl]);
  useEffect(() => {
    if (finished) return;
    if (!allowGreeting || reducedMotion()) {
      setFinished(true);
      return;
    }
    const video = videoRef.current;
    if (!video) return;
    let active = true;
    const finish = () => { if (active) setFinished(true); };
    const preference = window.matchMedia?.('(prefers-reduced-motion: reduce)');
    const onPreference = () => { if (preference?.matches) finish(); };
    const onVisibility = () => { if (document.hidden) finish(); };
    preference?.addEventListener('change', onPreference);
    document.addEventListener('visibilitychange', onVisibility);
    // A stalled download must not leave the visitor looking at an unfinished wave.
    const deadline = window.setTimeout(finish, 8000);
    video.muted = true;
    video.defaultMuted = true;
    try { void video.play().catch(finish); } catch { finish(); }
    return () => {
      active = false;
      window.clearTimeout(deadline);
      preference?.removeEventListener('change', onPreference);
      document.removeEventListener('visibilitychange', onVisibility);
      video.pause();
    };
  }, [allowGreeting, finished]);

  return <>
    {portraitFailed ? <div className="vincent-portrait-placeholder" role="img" aria-label="Vincent’s portrait is unavailable">
      <span className="vincent-monogram" aria-hidden="true">V.</span>
      <p>Vincent van Gogh</p>
      <span className="small">The portrait could not be loaded.</span>
    </div> : <img className="vincent-portrait" src={portraitUrl} width={834} height={1112}
      alt="An illustrated interpretation of Vincent van Gogh looking toward you."
      onError={() => setPortraitFailed(true)} />}
    {!finished && allowGreeting && <video ref={videoRef} className="vincent-greeting" src="/vincent/greeting.mp4"
      poster="/vincent/greeting-poster.webp" width={834} height={1112} autoPlay muted playsInline
      preload="auto" disablePictureInPicture disableRemotePlayback aria-hidden="true" tabIndex={-1}
      onEnded={() => setFinished(true)} onError={() => setFinished(true)} />}
  </>;
}
