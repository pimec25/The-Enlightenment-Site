"use strict";
const uploadForm = document.querySelector('form[enctype="multipart/form-data"]');
const uploadInput = uploadForm.querySelector('input[type="file"]');
const uploadNotice = document.getElementById('upload-size-notice');
const uploadButton = uploadForm.querySelector('button[type="submit"]');
const uploadLimit = Number(uploadForm.dataset.maxUploadBytes);
function checkUploadSize() {
  const file = uploadInput.files[0];
  const tooLarge = Boolean(file && file.size > uploadLimit);
  uploadButton.disabled = tooLarge;
  uploadNotice.hidden = !file;
  uploadNotice.className = tooLarge ? 'alert' : 'note';
  uploadNotice.textContent = file ? `${file.name}: ${(file.size / 1048576).toFixed(2)} MB. ` +
    (tooLarge ? `Maximum: ${uploadLimit / 1048576} MB. Choose a smaller .xlsx copy with the complete sales-detail data. This file has not been uploaded. Separate uploads are not combined for 80/20 or QUAD analysis.` : 'File size is within the upload limit.') : '';
  return !tooLarge;
}
uploadInput.addEventListener('change', checkUploadSize);
uploadForm.addEventListener('submit', event => {
  if (!checkUploadSize()) {
    event.preventDefault();
    uploadNotice.focus();
  }
});
