// AudioText Custom JavaScript

document.addEventListener('DOMContentLoaded', function () {
    // File upload preview
    const fileInput = document.getElementById('id_audio_file');
    const fileNameDisplay = document.getElementById('file-name-display');

    if (fileInput && fileNameDisplay) {
        fileInput.addEventListener('change', function (e) {
            if (this.files && this.files.length > 0) {
                const fileNames = Array.from(this.files).map(file => file.name).join(', ');
                fileNameDisplay.textContent = fileNames;
                fileNameDisplay.parentElement.classList.remove('d-none');
            } else {
                fileNameDisplay.textContent = '';
                fileNameDisplay.parentElement.classList.add('d-none');
            }
        });
    }

    // Drag and drop file upload
    const dropArea = document.getElementById('drop-area');
    if (dropArea && fileInput) {
        ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
            dropArea.addEventListener(eventName, preventDefaults, false);
        });

        function preventDefaults(e) {
            e.preventDefault();
            e.stopPropagation();
        }

        ['dragenter', 'dragover'].forEach(eventName => {
            dropArea.addEventListener(eventName, highlight, false);
        });

        ['dragleave', 'drop'].forEach(eventName => {
            dropArea.addEventListener(eventName, unhighlight, false);
        });

        function highlight() {
            dropArea.classList.add('highlight');
        }

        function unhighlight() {
            dropArea.classList.remove('highlight');
        }

        dropArea.addEventListener('drop', handleDrop, false);

        function handleDrop(e) {
            const dt = e.dataTransfer;
            const files = dt.files;

            if (files && files.length > 0) {
                fileInput.files = files;

                // Trigger change event
                const event = new Event('change', { bubbles: true });
                fileInput.dispatchEvent(event);
            }
        }
    }

    // Custom audio player functionality
    const audioPlayers = document.querySelectorAll('.audio-player');

    audioPlayers.forEach(player => {
        const audio = player.querySelector('audio');
        const playBtn = player.querySelector('.play-btn');
        const pauseBtn = player.querySelector('.pause-btn');
        const progress = player.querySelector('.progress');
        const currentTime = player.querySelector('.current-time');
        const duration = player.querySelector('.duration');

        if (audio && playBtn && pauseBtn && progress) {
            playBtn.addEventListener('click', () => {
                audio.play();
                playBtn.classList.add('d-none');
                pauseBtn.classList.remove('d-none');
            });

            pauseBtn.addEventListener('click', () => {
                audio.pause();
                pauseBtn.classList.add('d-none');
                playBtn.classList.remove('d-none');
            });

            audio.addEventListener('timeupdate', () => {
                const progressValue = (audio.currentTime / audio.duration) * 100;
                progress.style.width = progressValue + '%';

                if (currentTime) {
                    currentTime.textContent = formatTime(audio.currentTime);
                }
            });

            audio.addEventListener('ended', () => {
                pauseBtn.classList.add('d-none');
                playBtn.classList.remove('d-none');
                progress.style.width = '0%';
            });

            audio.addEventListener('loadedmetadata', () => {
                if (duration) {
                    duration.textContent = formatTime(audio.duration);
                }
            });
        }
    });

    function formatTime(seconds) {
        const min = Math.floor(seconds / 60);
        const sec = Math.floor(seconds % 60);
        return `${min}:${sec < 10 ? '0' + sec : sec}`;
    }

    // Bootstrap form validation
    const forms = document.querySelectorAll('.needs-validation');

    Array.from(forms).forEach(form => {
        form.addEventListener('submit', event => {
            if (!form.checkValidity()) {
                event.preventDefault();
                event.stopPropagation();
            }
            form.classList.add('was-validated');
        }, false);
    });

    // Initialize tooltips
    const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    tooltipTriggerList.map(function (tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });
});
