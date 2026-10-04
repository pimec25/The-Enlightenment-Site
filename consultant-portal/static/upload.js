"use strict";
const progressPage = document.querySelector('[data-large-job]');
const uploadForm = document.querySelector('form[enctype="multipart/form-data"]');
async function responseJSON(response) {
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'The request could not complete. Please reopen the agent from your dashboard.');
  return data;
}
if (progressPage) {
  const id = progressPage.dataset.largeJob;
  const message = document.getElementById('large-message');
  async function poll() {
    try {
      const state = await responseJSON(await fetch(`/consultant/large/${id}/status`, {cache:'no-store'}));
      message.textContent = state.message;
      if (state.state === 'ready' || state.state === 'saved') { location.reload(); return; }
      if (state.state === 'error') return;
      setTimeout(poll, 4000);
    } catch (error) { message.textContent = error.message + ' Refresh this page to check again.'; }
  }
  poll();
}
if (uploadForm) {
  const input = uploadForm.querySelector('input[type="file"]');
  const notice = document.getElementById('upload-size-notice');
  const button = uploadForm.querySelector('button[type="submit"]');
  let busy = false;
  function check() {
    const file = input.files[0];
    const excel = file && file.name.toLowerCase().endsWith('.xlsx');
    const limit = excel ? 100 * 1048576 : Number(uploadForm.dataset.maxUploadBytes);
    const tooLarge = Boolean(file && file.size > limit);
    button.disabled = busy || tooLarge;
    notice.hidden = !file;
    notice.className = tooLarge ? 'alert' : 'note';
    notice.textContent = file ? `${file.name}: ${(file.size/1048576).toFixed(2)} MB. ` + (tooLarge ? `Maximum: ${limit/1048576} MB. Choose a smaller file. Nothing has been uploaded.` : 'File size is within the upload limit.') : '';
    return !tooLarge;
  }
  input.addEventListener('change', check);
  uploadForm.addEventListener('submit', async event => {
    if (!check()) { event.preventDefault(); notice.focus(); return; }
    const file = input.files[0];
    if (!file || !file.name.toLowerCase().endsWith('.xlsx')) return;
    event.preventDefault();
    if (busy) return;
    busy = true; button.disabled = true; input.disabled = true;
    const csrf = uploadForm.querySelector('[name="csrf_token"]').value;
    const headers = {'X-CSRF-Token': csrf};
    try {
      notice.textContent = 'Preparing secure upload. Keep this page open.';
      const job = await responseJSON(await fetch('/consultant/large/start', {method:'POST',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify({filename:file.name,size:file.size,as_of:uploadForm.querySelector('[name="as_of"]').value})}));
      for (let offset=0; offset<file.size; offset+=job.chunk_bytes) {
        const piece = file.slice(offset, Math.min(file.size,offset+job.chunk_bytes));
        let failure;
        for (let attempt=0; attempt<3; attempt++) {
          try { await responseJSON(await fetch(`/consultant/large/${job.id}/chunk?offset=${offset}`,{method:'PUT',headers,body:piece})); failure=null; break; }
          catch(error) { failure=error; if(attempt<2) await new Promise(resolve=>setTimeout(resolve,1500)); }
        }
        if(failure) throw failure;
        notice.textContent = `Uploading ${Math.round(100*Math.min(file.size,offset+job.chunk_bytes)/file.size)}%. Keep this page open.`;
      }
      const ready = await responseJSON(await fetch(`/consultant/large/${job.id}/finish`,{method:'POST',headers}));
      location.assign(ready.url);
    } catch(error) {
      notice.className='alert'; notice.textContent=error.message + ' Your original file is unchanged. Select Upload and review to start again.';
      busy=false; button.disabled=false; input.disabled=false;
    }
  });
}
