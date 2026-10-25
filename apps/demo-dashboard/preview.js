/** Display-only clean images. Never used to authorize guidance. */
export class PreviewPresenter {
 constructor({decode, commit}) {
  this.decode=decode;this.commit=commit;this.hasFrame=false;
  this.current=null;this.pending=null;this.running=false;
 }
 update(src, receivedAt = null, mode = 'LIVE') {
  if (!src) return; // Keep the last image across slow frames and connection gaps.
  this.pending={src,receivedAt,mode};
  if (!this.running) void this.drain();
 }
 async drain() {
  this.running=true;
  try {
   while(this.pending) {
    const {src,receivedAt,mode}=this.pending;this.pending=null;
    if(src===this.current) {this.receivedAt=receivedAt;this.mode=mode;continue;}
    try {
     const image=await this.decode(src);
     // Replace only with a decoded image; the visible image never becomes blank.
     this.commit(image);this.current=src;this.receivedAt=receivedAt;this.mode=mode;this.hasFrame=true;
    } catch { /* A bad frame leaves the last successfully decoded image visible. */ }
   }
  } finally {this.running=false;}
 }
}
