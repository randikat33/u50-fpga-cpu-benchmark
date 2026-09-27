// === Live_Stream_CPU_bench.cpp ===  (thesis benchmark build)
// Identical to Live_Stream_CPU.cpp except for two additions:
//   1. CPU_PIPELINE_MODE=direct  -> frames go decode -> cv::resize -> encoder with NO
//      FPGA-style pack/unpack (the FPGA V5 host also eliminated pack/unpack, so this is
//      the like-for-like CPU baseline). Default (unset / "legacy") = original behaviour.
//   2. Millisecond-precision duration / FPS are printed in addition to the old
//      whole-second values.
//   3. SIGPIPE is ignored (the FPGA host already does this).
// CPU-only implementation that mirrors Live_Stream_Host.cpp pipeline ~1:1
// - Keeps: decoder, packers, queues, web control, ffmpeg audio mux
// - Replaces: FPGA kernel with CPU resize
// - Still simulates FPGA-style packed I/O: unpack->resize->pack->unpack

#include <iostream>
#include <vector>
#include <fstream>
#include <chrono>
#include <thread>
#include <mutex>
#include <condition_variable>
#include <atomic>
#include <algorithm>
#include <numeric>
#include <opencv2/opencv.hpp>
#include <microhttpd.h>
#include <unistd.h>
#include <cstdio>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <signal.h>
#include <iomanip>
#include <csignal>
#include <sys/wait.h>
#include <queue>
#include <cstring>
#include <omp.h>
#include <memory>

// -------------------- CONFIG --------------------
#define INPUT_WIDTH 3840
#define INPUT_HEIGHT 2160
#define PTR_WIDTH 256
#define PIXELS_PER_WORD 8
#define BYTES_PER_WORD 32
#define BYTES_PER_PIXEL 3

#define NUM_WORKERS 24
#define NUM_PACKER_THREADS 24
// Output resolutions (same as your host)
#define OUT_1080P_WIDTH 1920
#define OUT_1080P_HEIGHT 1080
#define OUT_720P_WIDTH 1280
#define OUT_720P_HEIGHT 720
#define OUT_480P_WIDTH 856
#define OUT_480P_HEIGHT 480
#define OUT_360P_WIDTH 640
#define OUT_360P_HEIGHT 360
#define OUT_240P_WIDTH 432
#define OUT_240P_HEIGHT 240

#define RAW_FRAME_QUEUE_SIZE 24
#define INPUT_QUEUE_SIZE 24
#define OUTPUT_QUEUE_SIZE 12

constexpr int INPUT_PIXELS = INPUT_WIDTH * INPUT_HEIGHT;
constexpr int INPUT_WORDS  = (INPUT_PIXELS + PIXELS_PER_WORD - 1) / PIXELS_PER_WORD;
constexpr size_t INPUT_BUFFER_SIZE = (size_t)INPUT_WORDS * BYTES_PER_WORD;

constexpr int MAX_OUT_PIXELS = OUT_1080P_WIDTH * OUT_1080P_HEIGHT;
constexpr int MAX_OUT_WORDS  = (MAX_OUT_PIXELS + PIXELS_PER_WORD - 1) / PIXELS_PER_WORD;
constexpr size_t MAX_OUT_BUFFER_SIZE = (size_t)MAX_OUT_WORDS * BYTES_PER_WORD;

std::atomic<double> globalAvgFps(0.0);
std::atomic<bool> shutdown_flag(false);
std::string g_videoPath;

static void signalHandler(int) {
    std::cout << "\nShutdown signal received.\n";
    shutdown_flag = true;
}

struct ResolutionConfig { int width, height; };
static std::vector<ResolutionConfig> resolutions = {
    {OUT_240P_WIDTH,  OUT_240P_HEIGHT},
    {OUT_360P_WIDTH,  OUT_360P_HEIGHT},
    {OUT_480P_WIDTH,  OUT_480P_HEIGHT},
    {OUT_720P_WIDTH,  OUT_720P_HEIGHT},
    {OUT_1080P_WIDTH, OUT_1080P_HEIGHT}
};

// ============== THREAD-SAFE QUEUE ==============
template<typename T>
class ThreadSafeQueue {
private:
    std::queue<T> queue;
    mutable std::mutex mtx;
    std::condition_variable cvNotEmpty;
    std::condition_variable cvNotFull;
    size_t maxSize;
    std::atomic<bool> finished{false};

public:
    ThreadSafeQueue(size_t size) : maxSize(size) {}

    bool push(T&& item) {
        std::unique_lock<std::mutex> lock(mtx);
        cvNotFull.wait(lock, [this] { return queue.size() < maxSize || finished || shutdown_flag; });
        if (finished || shutdown_flag) return false;
        queue.push(std::move(item));
        cvNotEmpty.notify_one();
        return true;
    }

    bool pop(T& item) {
        std::unique_lock<std::mutex> lock(mtx);
        cvNotEmpty.wait(lock, [this] { return !queue.empty() || finished || shutdown_flag; });
        if (queue.empty()) return false;
        item = std::move(queue.front());
        queue.pop();
        cvNotFull.notify_one();
        return true;
    }

    void setFinished() {
        finished = true;
        cvNotEmpty.notify_all();
        cvNotFull.notify_all();
    }

    bool isFinished() const { return finished && size() == 0; }

    size_t size() const {
        std::lock_guard<std::mutex> lock(mtx);
        return queue.size();
    }
};

// ============== BUFFER STRUCTURES ==============
struct PackedBuffer {
    std::vector<uint8_t> data;
    PackedBuffer() : data(INPUT_BUFFER_SIZE) {}
};


struct RawFrame {
    cv::Mat frame;
    int index;
    int quality;
};

struct InputFrame {
    int index;
    int quality;
    std::shared_ptr<PackedBuffer> packedData;
    double packMs;
    cv::Mat rawFrame;          // used only in CPU_PIPELINE_MODE=direct
};

static bool g_direct_mode = false;   // set from CPU_PIPELINE_MODE in main()

struct OutputFrame {
    cv::Mat frame;
    int index;
    int quality;

    // Kept same timing slots as FPGA version for easy compare
    double fpgaTotalMs, h2dMs, kernelMs, d2hMs;
    double packMs, unpackMs;

    // Extra CPU-only breakdown (optional display if you want)
    double inUnpackMs, resizeMs, outPackMs, outUnpackMs;
};

ThreadSafeQueue<RawFrame>   rawFrameQueue(RAW_FRAME_QUEUE_SIZE);
ThreadSafeQueue<InputFrame> inputQueue(INPUT_QUEUE_SIZE);
ThreadSafeQueue<OutputFrame> outputQueue(OUTPUT_QUEUE_SIZE);

// ============== BANDWIDTH MANAGER ==============
class BandwidthManager {
    std::atomic<int> currentQuality{4};
public:
    void setManualQuality(int q) { currentQuality = q; }
    int getCurrentQuality() { return currentQuality.load(); }
    std::string getQualityName(int q) {
        const char* names[] = {"240p", "360p", "480p", "720p", "1080p"};
        return (q >= 0 && q <= 4) ? names[q] : "Unknown";
    }
};

BandwidthManager bwManager;

// ============== BUFFER POOL ==============
template<typename T>
class BufferPool {
private:
    std::queue<std::shared_ptr<T>> pool;
    std::mutex mtx;
    int maxSize;

public:
    BufferPool(int size) : maxSize(size) {
        for (int i = 0; i < size; i++) pool.push(std::make_shared<T>());
    }

    std::shared_ptr<T> acquire() {
        std::lock_guard<std::mutex> lock(mtx);
        if (pool.empty()) return std::make_shared<T>();
        auto buf = pool.front();
        pool.pop();
        return buf;
    }

    void release(std::shared_ptr<T> buf) {
        std::lock_guard<std::mutex> lock(mtx);
        if ((int)pool.size() < maxSize * 2) pool.push(buf);
    }
};

BufferPool<PackedBuffer> bufferPool(INPUT_QUEUE_SIZE * 2);

// ============== ULTRA-FAST PIXEL PACKING (UNCHANGED) ==============
void pack_frame_ultrafast(const cv::Mat& img, uint8_t* __restrict__ out) {
    const int rows = img.rows;
    const int cols = img.cols;
    const int total_pixels = rows * cols;
    const int total_words = (total_pixels + PIXELS_PER_WORD - 1) / PIXELS_PER_WORD;

    const uint8_t* __restrict__ src = img.data;
    const size_t stride = img.step[0];
    const bool contiguous = (stride == (size_t)(cols * 3));

    memset(out, 0, (size_t)total_words * BYTES_PER_WORD);

    // 24 outer workers = 1 worker per physical core on Socket 0.
    // No inner OMP parallelism in this packing routine.
    for (int word_idx = 0; word_idx < total_words; word_idx++) {
        int pixel_start = word_idx * PIXELS_PER_WORD;
        int pixels_in_word = std::min(PIXELS_PER_WORD, total_pixels - pixel_start);

        uint8_t* dst_word = out + (size_t)word_idx * BYTES_PER_WORD;

        if (contiguous && pixels_in_word == 8) {
            const uint8_t* src_pixels = src + (size_t)pixel_start * 3;

            for (int i = 0; i < 8; i++) {
                const uint8_t* px = src_pixels + i * 3;
                int bit_offset = i * 24;
                int byte_offset = bit_offset / 8;
                int bit_shift = bit_offset % 8;

                uint32_t val = ((uint32_t)px[0] << 16) | ((uint32_t)px[1] << 8) | px[2];

                if (bit_shift == 0) {
                    dst_word[byte_offset]     = val & 0xFF;
                    dst_word[byte_offset + 1] = (val >> 8) & 0xFF;
                    dst_word[byte_offset + 2] = (val >> 16) & 0xFF;
                } else {
                    // Write only the bytes actually touched; no cross-word overlap
                    // because each pixel is 24 bits and words are 32 bytes, so
                    // byte_offset+3 is always within [0,31].
                    uint32_t shifted = val << bit_shift;
                    dst_word[byte_offset]     |= (shifted) & 0xFF;
                    dst_word[byte_offset + 1] |= (shifted >> 8) & 0xFF;
                    dst_word[byte_offset + 2] |= (shifted >> 16) & 0xFF;
                    dst_word[byte_offset + 3] |= (shifted >> 24) & 0xFF;
                }
            }
        } else {
            for (int j = 0; j < pixels_in_word; j++) {
                int px_idx = pixel_start + j;
                const uint8_t* px;

                if (contiguous) {
                    px = src + (size_t)px_idx * 3;
                } else {
                    int row = px_idx / cols;
                    int col = px_idx % cols;
                    px = src + (size_t)row * stride + col * 3;
                }

                int bit_offset = j * 24;
                int byte_offset = bit_offset / 8;
                int bit_shift = bit_offset % 8;

                uint32_t val = ((uint32_t)px[0] << 16) | ((uint32_t)px[1] << 8) | px[2];

                if (bit_shift == 0) {
                    dst_word[byte_offset]     = val & 0xFF;
                    dst_word[byte_offset + 1] = (val >> 8) & 0xFF;
                    dst_word[byte_offset + 2] = (val >> 16) & 0xFF;
                } else {
                    uint32_t shifted = val << bit_shift;
                    dst_word[byte_offset]     |= (shifted) & 0xFF;
                    dst_word[byte_offset + 1] |= (shifted >> 8) & 0xFF;
                    dst_word[byte_offset + 2] |= (shifted >> 16) & 0xFF;
                    dst_word[byte_offset + 3] |= (shifted >> 24) & 0xFF;
                }
            }
        }
    }
}

void unpack_frame_ultrafast(const uint8_t* __restrict__ src, cv::Mat& img, int w, int h) {
    const int total_pixels = w * h;
    const int total_words = (total_pixels + PIXELS_PER_WORD - 1) / PIXELS_PER_WORD;

    uint8_t* __restrict__ dst = img.data;
    const size_t stride = img.step[0];
    const bool contiguous = (stride == (size_t)(w * 3));

    // No inner OMP parallelism: same reason as pack_frame_ultrafast.
    for (int word_idx = 0; word_idx < total_words; word_idx++) {
        int pixel_start = word_idx * PIXELS_PER_WORD;
        const uint8_t* src_word = src + (size_t)word_idx * BYTES_PER_WORD;

        for (int j = 0; j < PIXELS_PER_WORD; j++) {
            int px_idx = pixel_start + j;
            if (px_idx >= total_pixels) break;

            int bit_offset = j * 24;
            int byte_offset = bit_offset / 8;
            int bit_shift = bit_offset % 8;

            uint32_t val;
            if (bit_shift == 0) {
                val = src_word[byte_offset] |
                      ((uint32_t)src_word[byte_offset + 1] << 8) |
                      ((uint32_t)src_word[byte_offset + 2] << 16);
            } else {
                // Safe byte-by-byte read avoids out-of-bounds 4-byte access
                // at the end of the last word in the buffer.
                uint32_t raw = (uint32_t)src_word[byte_offset] |
                               ((uint32_t)src_word[byte_offset + 1] << 8) |
                               ((uint32_t)src_word[byte_offset + 2] << 16) |
                               ((uint32_t)src_word[byte_offset + 3] << 24);
                val = (raw >> bit_shift) & 0xFFFFFF;
            }

            uint8_t* px;
            if (contiguous) {
                px = dst + (size_t)px_idx * 3;
            } else {
                int row = px_idx / w;
                int col = px_idx % w;
                px = dst + (size_t)row * stride + col * 3;
            }

            px[0] = (val >> 16) & 0xFF;
            px[1] = (val >> 8) & 0xFF;
            px[2] = val & 0xFF;
        }
    }
}

// ============== FFMPEG PIPE WITH AUDIO (UNCHANGED LOGIC) ==============
class FFmpegPipeWithAudio {
private:
    FILE* pipes[5] = {nullptr};
    pid_t pids[5] = {-1};
    std::string videoPath;
    int sourceFps;

public:
    FFmpegPipeWithAudio(const std::string& srcVideo, int fps)
        : videoPath(srcVideo), sourceFps(fps) {

        const char* sizes[] = {"426x240", "640x360", "856x480", "1280x720", "1920x1080"};
        const char* names[] = {"240p", "360p", "480p", "720p", "1080p"};

        for (int i = 0; i < 5; i++) {
            int fd[2];
            if (pipe(fd)) continue;

            pid_t pid = fork();
            if (pid == 0) {
                close(fd[1]);
                dup2(fd[0], STDIN_FILENO);
                close(fd[0]);

                char cmd[2048];
                snprintf(cmd, sizeof(cmd),
                    "ffmpeg -y "
                    "-f rawvideo -pix_fmt bgr24 -s %s -r %d -i pipe:0 "
                    "-i \"%s\" "
                    "-map 0:v:0 -map 1:a:0? "
                    "-c:v libx264 -preset ultrafast -tune zerolatency "
                    "-c:a aac -b:a 128k "
                    "-shortest "
                    "-f flv rtmp://localhost/live/stream_%s "
                    "2>/dev/null",
                    sizes[i], fps,
                    videoPath.c_str(),
                    names[i]
                );

                execl("/bin/sh", "sh", "-c", cmd, NULL);
                exit(1);
            }

            close(fd[0]);
            pipes[i] = fdopen(fd[1], "wb");
            if (pipes[i]) setvbuf(pipes[i], nullptr, _IOFBF, 4 * 1024 * 1024);
            pids[i] = pid;
        }
    }

    ~FFmpegPipeWithAudio() {
        for (int i = 0; i < 5; i++) {
            if (pipes[i]) fclose(pipes[i]);
            if (pids[i] > 0) {
                kill(pids[i], SIGTERM);
                waitpid(pids[i], nullptr, 0);
            }
        }
    }

    void writeFrame(const cv::Mat& frame, int quality) {
        if (quality >= 0 && quality <= 4 && pipes[quality]) {
            fwrite(frame.data, 1, frame.total() * frame.elemSize(), pipes[quality]);
        }
    }
};

// ============== WEB INTERFACE (UNCHANGED LOGIC) ==============
class WebInterface {
    struct MHD_Daemon* daemon = nullptr;

    static MHD_Result handler(void*, struct MHD_Connection* conn, const char* url,
                              const char*, const char*, const char*, size_t*, void**) {
        std::string response;
        if (strstr(url, "/quality/")) {
            int q = atoi(url + 9);
            if (q >= 0 && q <= 4) bwManager.setManualQuality(q);
            response = "Quality set to " + bwManager.getQualityName(q);
        } else {
            char buf[512];
            snprintf(buf, sizeof(buf),
                "FPS: %.1f | Quality: %s | RawQ: %zu | InQ: %zu | OutQ: %zu",
                globalAvgFps.load(),
                bwManager.getQualityName(bwManager.getCurrentQuality()).c_str(),
                rawFrameQueue.size(), inputQueue.size(), outputQueue.size());
            response = std::string(
                "<html><body>"
                "<h1>CPU Live Stream Control (Audio ON)</h1>"
                "<p>") + buf + "</p>"
                "<h2>Quality Selection:</h2>"
                "<a href='/quality/4'>1080p</a> | "
                "<a href='/quality/3'>720p</a> | "
                "<a href='/quality/2'>480p</a> | "
                "<a href='/quality/1'>360p</a> | "
                "<a href='/quality/0'>240p</a>"
                "<h2>Stream URLs:</h2>"
                "<ul>"
                "<li>1080p: rtmp://localhost/live/stream_1080p</li>"
                "<li>720p: rtmp://localhost/live/stream_720p</li>"
                "<li>480p: rtmp://localhost/live/stream_480p</li>"
                "<li>360p: rtmp://localhost/live/stream_360p</li>"
                "<li>240p: rtmp://localhost/live/stream_240p</li>"
                "</ul>"
                "</body></html>";

        }

        auto* resp = MHD_create_response_from_buffer(response.size(),
                                                     (void*)response.c_str(),
                                                     MHD_RESPMEM_MUST_COPY);
        int ret = MHD_queue_response(conn, MHD_HTTP_OK, resp);
        MHD_destroy_response(resp);
        return (MHD_Result)ret;
    }

public:
    void start(int port = 8888) {
        daemon = MHD_start_daemon(MHD_USE_SELECT_INTERNALLY, port, NULL, NULL, &handler, NULL, MHD_OPTION_END);
    }
    void stop() { if (daemon) MHD_stop_daemon(daemon); }
};

// ============== CPU WORKER (FPGA REPLACEMENT) ==============
class CpuWorker {
private:
    int workerId;
    std::thread workerThread;
    std::atomic<int> framesProcessed{0};

    // Local output packed scratch (max 1080p packed)
    std::vector<uint8_t> outPacked;


public:
    explicit CpuWorker(int id) : workerId(id), outPacked(MAX_OUT_BUFFER_SIZE) {}

    void start() { workerThread = std::thread(&CpuWorker::run, this); }
    void join()  { if (workerThread.joinable()) workerThread.join(); }
    int getFramesProcessed() { return framesProcessed.load(); }

private:
    void run() {
        std::cout << "CPU Worker " << workerId << " started" << std::endl;

        // Reuse a 4K Mat buffer to reduce alloc churn
        cv::Mat input4k(INPUT_HEIGHT, INPUT_WIDTH, CV_8UC3);

        while (!shutdown_flag) {
            InputFrame input;
            if (!inputQueue.pop(input)) {
                if (inputQueue.isFinished()) break;
                continue;
            }

            const double packMs = input.packMs;

            int out_w = resolutions[input.quality].width;
            int out_h = resolutions[input.quality].height;

            if (g_direct_mode) {
                // ---- direct path: raw decoded frame -> resize -> encoder
                auto d0 = std::chrono::high_resolution_clock::now();
                cv::Mat resizedD(out_h, out_w, CV_8UC3);
                cv::resize(input.rawFrame, resizedD, cv::Size(out_w, out_h), 0, 0, cv::INTER_LINEAR);
                auto d1 = std::chrono::high_resolution_clock::now();
                double resizeMsD = std::chrono::duration<double, std::milli>(d1 - d0).count();
                input.rawFrame.release();

                cv::Mat croppedD;
                if (input.quality == 0 && resizedD.cols > 426) {
                    int cx = (resizedD.cols - 426) / 2;
                    croppedD = resizedD(cv::Rect(cx, 0, 426, 240)).clone();
                } else if (input.quality == 2 && resizedD.cols > 856) {
                    int cx = (resizedD.cols - 856) / 2;
                    croppedD = resizedD(cv::Rect(cx, 0, 856, 480)).clone();
                } else {
                    croppedD = resizedD;
                }
                OutputFrame od;
                od.frame = croppedD;
                od.index = input.index;
                od.quality = input.quality;
                od.h2dMs = 0.0; od.kernelMs = resizeMsD; od.d2hMs = 0.0;
                od.fpgaTotalMs = resizeMsD;
                od.packMs = 0.0; od.unpackMs = 0.0;
                od.inUnpackMs = 0.0; od.resizeMs = resizeMsD;
                od.outPackMs = 0.0; od.outUnpackMs = 0.0;
                outputQueue.push(std::move(od));
                framesProcessed++;
                continue;
            }

            // ---- (1) unpack packed input -> 4K Mat
            auto t0 = std::chrono::high_resolution_clock::now();
            unpack_frame_ultrafast(input.packedData->data.data(), input4k, INPUT_WIDTH, INPUT_HEIGHT);
            auto t1 = std::chrono::high_resolution_clock::now();
            double inUnpackMs = std::chrono::duration<double, std::milli>(t1 - t0).count();

            // release input packed buffer back to pool early (like FPGA host does after readback)
            bufferPool.release(input.packedData);

            // ---- (2) resize on CPU
            auto t2 = std::chrono::high_resolution_clock::now();
            cv::Mat resized(out_h, out_w, CV_8UC3);
            cv::resize(input4k, resized, cv::Size(out_w, out_h), 0, 0, cv::INTER_LINEAR);
            auto t3 = std::chrono::high_resolution_clock::now();
            double resizeMs = std::chrono::duration<double, std::milli>(t3 - t2).count();

            // ---- (3) pack resized -> packed output (simulate FPGA packed out)
            auto t4 = std::chrono::high_resolution_clock::now();
            pack_frame_ultrafast(resized, outPacked.data());
            auto t5 = std::chrono::high_resolution_clock::now();
            double outPackMs = std::chrono::duration<double, std::milli>(t5 - t4).count();

            // ---- (4) unpack packed output -> Mat (simulate host unpack after D2H)
            auto t6 = std::chrono::high_resolution_clock::now();
            cv::Mat output(out_h, out_w, CV_8UC3);
            unpack_frame_ultrafast(outPacked.data(), output, out_w, out_h);
            auto t7 = std::chrono::high_resolution_clock::now();
            double outUnpackMs = std::chrono::duration<double, std::milli>(t7 - t6).count();

            // Keep your cropping rules (for FFmpeg expected sizes)
            cv::Mat cropped;
            if (input.quality == 0 && output.cols > 426) {
                int cx = (output.cols - 426) / 2;
                cropped = output(cv::Rect(cx, 0, 426, 240)).clone();
            } else if (input.quality == 2 && output.cols > 856) {
                int cx = (output.cols - 856) / 2;
                cropped = output(cv::Rect(cx, 0, 856, 480)).clone();
            } else {
                cropped = output;
            }

            // Populate OutputFrame keeping same fields
            OutputFrame outFrame;
            outFrame.frame = cropped;
            outFrame.index = input.index;
            outFrame.quality = input.quality;

            // In FPGA host: fpgaTotalMs = H2D + Kernel + D2H
            // Here we map kernelMs to CPU resize time, and keep H2D/D2H as 0.
            outFrame.h2dMs = 0.0;
            outFrame.kernelMs = resizeMs;
            outFrame.d2hMs = 0.0;
            outFrame.fpgaTotalMs = outFrame.h2dMs + outFrame.kernelMs + outFrame.d2hMs;

            // In your host print: CPU PACK/UNPACK (these were host-side pack/unpack around FPGA)
            // Here we provide both input unpack and output pack/unpack too.
            outFrame.packMs = packMs;               // packer stage (raw->packed)
            outFrame.unpackMs = outUnpackMs;        // final unpack (packed->Mat)

            outFrame.inUnpackMs = inUnpackMs;
            outFrame.resizeMs   = resizeMs;
            outFrame.outPackMs  = outPackMs;
            outFrame.outUnpackMs= outUnpackMs;

            outputQueue.push(std::move(outFrame));
            framesProcessed++;
        }

        std::cout << "CPU Worker " << workerId << " finished (" << framesProcessed << " frames)" << std::endl;
    }
};

// ============== MAIN ==============
int main(int argc, char* argv[]) {
    signal(SIGINT, signalHandler);
    signal(SIGTERM, signalHandler);

    std::cout << "========================================\n";
    std::cout << "  CPU Live Stream Resizer (FPGA-equivalent pipeline)\n";
    std::cout << "  Audio ON | Web Control ON\n";
    std::cout << "========================================\n";

    // NEW: only <video_file>
    if (argc < 2) {
        std::cerr << "Usage: " << argv[0] << " <video_file>\n";
        std::cerr << "  Set STREAM_QUALITY=0..4 to select output quality:\n";
        std::cerr << "    0=240p  1=360p  2=480p  3=720p  4=1080p (default)\n";
        return EXIT_FAILURE;
    }

    const std::string videoPath = argv[1];
    g_videoPath = videoPath;

    // Read quality index from environment variable set by run_benchmark.sh:
    //   export STREAM_QUALITY=${quality_idx}
    // Values: 0=240p, 1=360p, 2=480p, 3=720p, 4=1080p (default)
    signal(SIGPIPE, SIG_IGN);   // same as the FPGA V5 host: a closed FFmpeg pipe must not kill the run
    {
        const char* pm = std::getenv("CPU_PIPELINE_MODE");
        g_direct_mode = (pm != nullptr && std::string(pm) == "direct");
        std::cout << "CPU_PIPELINE_MODE: " << (g_direct_mode ? "direct (no pack/unpack)" : "legacy (FPGA-style pack/unpack)") << "\n";
    }
    const char* envQuality = std::getenv("STREAM_QUALITY");
    if (envQuality != nullptr) {
        int q = std::atoi(envQuality);
        if (q >= 0 && q <= 4) {
            bwManager.setManualQuality(q);
            std::cout << "Quality set to: " << bwManager.getQualityName(q)
                      << " (STREAM_QUALITY=" << q << ")\n";
        } else {
            std::cerr << "Warning: STREAM_QUALITY=" << envQuality
                      << " out of range [0-4], defaulting to 1080p\n";
        }
    } else {
        std::cout << "Quality: 1080p (default — set STREAM_QUALITY=0..4 to override)\n";
    }

    cv::VideoCapture video(videoPath);
    if (!video.isOpened()) {
        std::cerr << "Could not open video: " << videoPath << "\n";
        return EXIT_FAILURE;
    }
    video.set(cv::CAP_PROP_HW_ACCELERATION, cv::VIDEO_ACCELERATION_ANY);

    int width = (int)video.get(cv::CAP_PROP_FRAME_WIDTH);
    int height = (int)video.get(cv::CAP_PROP_FRAME_HEIGHT);
    int fps = (int)video.get(cv::CAP_PROP_FPS);
    int totalFrames = (int)video.get(cv::CAP_PROP_FRAME_COUNT);

    if (width != INPUT_WIDTH || height != INPUT_HEIGHT) {
        std::cerr << "Video is not 4K. Found: " << width << "x" << height << "\n";
        return EXIT_FAILURE;
    }

    std::cout << "Video: " << width << "x" << height << " @ " << fps << " fps, "
              << totalFrames << " frames\n";
    std::cout << "Audio: muxed from source by FFmpeg\n";

    WebInterface webInterface;
    webInterface.start();
    std::cout << "Web interface: http://localhost:8888\n";

    std::cout << "\nStarting FFmpeg with audio muxing...\n";
    FFmpegPipeWithAudio ffmpegPipe(videoPath, fps);

    std::cout << "\nStarting pipeline...\n";

    // Workers
    std::vector<std::unique_ptr<CpuWorker>> workers;
    for (int i = 0; i < NUM_WORKERS; i++) workers.push_back(std::make_unique<CpuWorker>(i));
    for (auto& w : workers) w->start();

    auto startTime = std::chrono::high_resolution_clock::now();
    std::atomic<int> decodedFrames{0};

    // Decoder thread
    std::thread decoderThread([&video, &decodedFrames]() {
        std::cout << "Decoder thread started\n";
        cv::Mat frame;
        int idx = 0;

        while (video.read(frame) && !shutdown_flag) {
            if (frame.cols != INPUT_WIDTH || frame.rows != INPUT_HEIGHT) continue;

            RawFrame raw;
            raw.frame = frame.clone(); // clone: video.read() reuses the same buffer each iteration
            raw.index = idx++;
            raw.quality = bwManager.getCurrentQuality();

            if (!rawFrameQueue.push(std::move(raw))) break;
            decodedFrames++;
        }
        rawFrameQueue.setFinished();
        std::cout << "Decoder finished (" << idx << " frames)\n";
    });

    // Packer threads
    std::vector<std::thread> packerThreads;
    std::atomic<int> packedFrames{0};

    for (int t = 0; t < NUM_PACKER_THREADS; t++) {
        packerThreads.emplace_back([t, &packedFrames]() {
            std::cout << "Packer " << t << " started\n";

            while (!shutdown_flag) {
                RawFrame raw;
                if (!rawFrameQueue.pop(raw)) {
                    if (rawFrameQueue.isFinished()) break;
                    continue;
                }

                if (g_direct_mode) {
                    InputFrame din;
                    din.index = raw.index;
                    din.quality = raw.quality;
                    din.packMs = 0.0;
                    din.rawFrame = std::move(raw.frame);
                    if (!inputQueue.push(std::move(din))) break;
                    packedFrames++;
                    continue;
                }

                auto packedData = bufferPool.acquire();

                auto tpack0 = std::chrono::high_resolution_clock::now();
                pack_frame_ultrafast(raw.frame, packedData->data.data());
                auto tpack1 = std::chrono::high_resolution_clock::now();
                double packMs = std::chrono::duration<double, std::milli>(tpack1 - tpack0).count();

                InputFrame input;
                input.index = raw.index;
                input.quality = raw.quality;
                input.packedData = packedData;
                input.packMs = packMs;

                if (!inputQueue.push(std::move(input))) break;
                packedFrames++;
            }

            std::cout << "Packer " << t << " finished\n";
        });
    }

    // Output consumer
    std::atomic<int> outputCount{0};
    double totalKernel = 0;
    double totalPack = 0, totalOutUnpack = 0;

    auto lastPrint = std::chrono::high_resolution_clock::now();
    int printCounter = 0;

    std::thread consumerThread([&]() {
        std::cout << "Consumer started (audio muxing)\n";

        while (!shutdown_flag) {
            OutputFrame output;
            if (!outputQueue.pop(output)) {
                if (outputQueue.isFinished()) break;
                std::this_thread::sleep_for(std::chrono::milliseconds(1));
                continue;
            }

            ffmpegPipe.writeFrame(output.frame, output.quality);

            outputCount++;
            totalKernel += output.kernelMs;
            totalPack += output.packMs;
            totalOutUnpack += output.unpackMs;

            printCounter++;

            auto now = std::chrono::high_resolution_clock::now();
            auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(now - lastPrint).count();
            if (elapsed >= 1000) {
                double fpsNow = printCounter * 1000.0 / elapsed;
                globalAvgFps.store(fpsNow);

                std::cout << "\rFPS: " << std::fixed << std::setprecision(1) << fpsNow
                          << " | Quality: " << bwManager.getQualityName(output.quality)
                          << " | CPU(ms) RESIZE:" << std::setprecision(2) << output.kernelMs
                          << " PACK:" << output.packMs
                          << " OUT_UNPACK:" << output.unpackMs
                          << " | RawQ:" << rawFrameQueue.size()
                          << " InQ:" << inputQueue.size()
                          << " OutQ:" << outputQueue.size()
                          << " | [AUDIO ON]"
                          << "    " << std::flush;

                printCounter = 0;
                lastPrint = now;
            }
        }

        std::cout << "\nConsumer finished\n";
    });

    // Wait
    decoderThread.join();
    for (auto& t : packerThreads) t.join();
    inputQueue.setFinished();

    for (auto& w : workers) w->join();
    outputQueue.setFinished();

    consumerThread.join();
    webInterface.stop();

    auto endTime = std::chrono::high_resolution_clock::now();
    auto duration = std::chrono::duration_cast<std::chrono::seconds>(endTime - startTime).count();
    double durationMs = std::chrono::duration<double, std::milli>(endTime - startTime).count();

    // Summary
    std::cout << "\n\n========================================\n";
    std::cout << "  CPU Processing Complete\n";
    std::cout << "========================================\n";
    std::cout << "Quality: " << bwManager.getQualityName(bwManager.getCurrentQuality()) << "\n";
    std::cout << "Total frames: " << outputCount << "\n";
    std::cout << "Duration: " << duration << " seconds\n";
    std::cout << "Precise duration: " << std::fixed << std::setprecision(3) << durationMs / 1000.0 << " s\n";
    std::cout << "Precise FPS: " << std::fixed << std::setprecision(3)
              << (durationMs > 0 ? outputCount * 1000.0 / durationMs : 0.0) << "\n";
    std::cout << "Average FPS: " << std::fixed << std::setprecision(2)
              << (double)outputCount / (duration > 0 ? duration : 1) << "\n";

    if (outputCount > 0) {
        std::cout << "Avg CPU RESIZE: " << (totalKernel / outputCount) << " ms\n";
        std::cout << "Avg CPU PACK (raw->packed): " << (totalPack / outputCount) << " ms\n";
        std::cout << "Avg CPU OUT_UNPACK: " << (totalOutUnpack / outputCount) << " ms\n";
    }

    std::cout << "\nPer-worker stats:\n";
    for (int i = 0; i < NUM_WORKERS; i++) {
        std::cout << "  Worker " << i << ": " << workers[i]->getFramesProcessed() << " frames\n";
    }

    return 0;
}

