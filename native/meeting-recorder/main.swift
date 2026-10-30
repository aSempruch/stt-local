// stt-local-meeting-recorder: records everything the Mac plays (a global
// Core Audio process tap) and the default microphone into two aligned files
// of 16 kHz mono little-endian int16 samples, for STT Local's meeting
// transcription.
//
// Usage: stt-local-meeting-recorder --out <dir> --exclude-pid <pid>
//
// Events go to stdout as JSON lines (started, mic_changed, mic_stalled,
// system_silent, error). Closing stdin, SIGTERM or SIGINT stops the recording
// and writes recording.json. The output device is never opened or changed:
// the tap sits in a private aggregate device anchored on the built-in output,
// so Bluetooth headsets keep their connection as it is.
import AVFoundation
import AudioToolbox
import CoreAudio
import Foundation

let sampleRate = 16_000.0
let toleranceSamples: Int64 = 160  // 10 ms
let micStallSeconds = 5.0
let systemSilentSeconds = 10.0

signal(SIGPIPE, SIG_IGN)

// MARK: - Arguments

var outputDirectory: URL?
var excludedPID: pid_t = 0
var arguments = CommandLine.arguments.dropFirst().makeIterator()
while let argument = arguments.next() {
    switch argument {
    case "--out": outputDirectory = arguments.next().map { URL(fileURLWithPath: $0) }
    case "--exclude-pid": excludedPID = pid_t(arguments.next() ?? "") ?? 0
    default: break
    }
}

// MARK: - Events

let emitLock = NSLock()

func emit(_ fields: [String: Any]) {
    guard var data = try? JSONSerialization.data(withJSONObject: fields, options: [.sortedKeys]) else { return }
    data.append(0x0A)
    emitLock.lock()
    defer { emitLock.unlock() }
    // POSIX write: FileHandle raises an exception once the app has gone.
    data.withUnsafeBytes { buffer in
        var offset = 0
        while offset < buffer.count {
            let written = write(1, buffer.baseAddress! + offset, buffer.count - offset)
            if written <= 0 { return }
            offset += written
        }
    }
}

func fail(_ message: String) -> Never {
    emit(["event": "error", "message": message])
    cleanUp()
    exit(1)
}

let directory: URL = {
    guard let outputDirectory else {
        emit(["event": "error", "message": "Usage: stt-local-meeting-recorder --out <dir> --exclude-pid <pid>"])
        exit(2)
    }
    return outputDirectory
}()
try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)

// MARK: - Host time

var timebase = mach_timebase_info_data_t()
mach_timebase_info(&timebase)

func hostSeconds(_ hostTime: UInt64) -> Double {
    Double(hostTime) * Double(timebase.numer) / Double(timebase.denom) / 1e9
}

let originLock = NSLock()
var originHostTime: UInt64 = 0

/// Both tracks count from the host time of the first buffer either delivers.
func origin(claiming hostTime: UInt64) -> UInt64 {
    originLock.lock()
    defer { originLock.unlock() }
    if originHostTime == 0 { originHostTime = hostTime }
    return originHostTime
}

func secondsSinceOrigin() -> Double {
    originLock.lock()
    let start = originHostTime
    originLock.unlock()
    return start == 0 ? 0 : max(0, hostSeconds(mach_absolute_time()) - hostSeconds(start))
}

func rounded(_ seconds: Double) -> Double { (seconds * 10).rounded() / 10 }

// MARK: - Core Audio helpers

let systemObject = AudioObjectID(kAudioObjectSystemObject)

func address(_ selector: AudioObjectPropertySelector, _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) -> AudioObjectPropertyAddress {
    AudioObjectPropertyAddress(mSelector: selector, mScope: scope, mElement: kAudioObjectPropertyElementMain)
}

func readString(_ id: AudioObjectID, _ selector: AudioObjectPropertySelector) -> String {
    var a = address(selector)
    var value: Unmanaged<CFString>?
    var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
    guard AudioObjectGetPropertyData(id, &a, 0, nil, &size, &value) == noErr else { return "" }
    return (value?.takeRetainedValue() as String?) ?? ""
}

func readUInt32(_ id: AudioObjectID, _ selector: AudioObjectPropertySelector, _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) -> UInt32 {
    var a = address(selector, scope)
    var value = UInt32(0)
    var size = UInt32(MemoryLayout<UInt32>.size)
    AudioObjectGetPropertyData(id, &a, 0, nil, &size, &value)
    return value
}

func readObjectIDs(_ id: AudioObjectID, _ selector: AudioObjectPropertySelector) -> [AudioObjectID] {
    var a = address(selector)
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(id, &a, 0, nil, &size) == noErr, size > 0 else { return [] }
    var ids = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
    guard AudioObjectGetPropertyData(id, &a, 0, nil, &size, &ids) == noErr else { return [] }
    return ids
}

func defaultDevice(_ selector: AudioObjectPropertySelector) -> AudioDeviceID {
    readUInt32(systemObject, selector)
}

func streamFormat(_ id: AudioObjectID, _ selector: AudioObjectPropertySelector, _ scope: AudioObjectPropertyScope) -> AudioStreamBasicDescription {
    var format = AudioStreamBasicDescription()
    var size = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
    var a = address(selector, scope)
    AudioObjectGetPropertyData(id, &a, 0, nil, &size, &format)
    return format
}

/// The built-in speakers, which anchor the tap's aggregate device without
/// touching whatever output the user is listening on.
func builtInOutput() -> AudioDeviceID {
    for id in readObjectIDs(systemObject, kAudioHardwarePropertyDevices) {
        var streams = address(kAudioDevicePropertyStreams, kAudioObjectPropertyScopeOutput)
        var size: UInt32 = 0
        AudioObjectGetPropertyDataSize(id, &streams, 0, nil, &size)
        if readUInt32(id, kAudioDevicePropertyTransportType) == kAudioDeviceTransportTypeBuiltIn && size > 0 {
            return id
        }
    }
    return defaultDevice(kAudioHardwarePropertyDefaultOutputDevice)
}

func processObject(for pid: pid_t) -> AudioObjectID {
    guard pid > 0 else { return AudioObjectID(kAudioObjectUnknown) }
    var a = address(kAudioHardwarePropertyTranslatePIDToProcessObject)
    var pid = pid
    var object = AudioObjectID(kAudioObjectUnknown)
    var size = UInt32(MemoryLayout<AudioObjectID>.size)
    AudioObjectGetPropertyData(systemObject, &a, UInt32(MemoryLayout<pid_t>.size), &pid, &size, &object)
    return object
}

/// Whether a process other than this helper and the excluded app is playing.
func othersPlaying() -> Bool {
    let own = getpid()
    for process in readObjectIDs(systemObject, kAudioHardwarePropertyProcessObjectList) {
        var a = address(kAudioProcessPropertyPID)
        var pid = pid_t(0)
        var size = UInt32(MemoryLayout<pid_t>.size)
        AudioObjectGetPropertyData(process, &a, 0, nil, &size, &pid)
        if pid == own || pid == excludedPID { continue }
        if readUInt32(process, kAudioProcessPropertyIsRunningOutput) != 0 { return true }
    }
    return false
}

// MARK: - Tracks

/// One output file. IOProcs copy channel 0 and hand it to a serial queue,
/// which converts it to 16 kHz and places it by host time: a gap becomes
/// silence and an overlap is dropped, which also absorbs clock drift.
final class Track {
    let name: String
    let queue: DispatchQueue
    let descriptor: Int32
    private var described = false  // touched only on the IO thread
    private let stateLock = NSLock()
    private var lastBuffer: UInt64 = 0
    private var lastSound: UInt64 = 0
    private var written: Int64 = 0  // touched only on queue
    private var converter: AVAudioConverter?  // touched only on queue
    private var converterRate = 0.0  // touched only on queue
    private let outputFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: sampleRate, channels: 1, interleaved: false)!

    init(_ name: String) {
        self.name = name
        queue = DispatchQueue(label: "track.\(name)", qos: .userInitiated)
        let path = directory.appendingPathComponent("\(name).pcm").path
        descriptor = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0o644)
        if descriptor < 0 { fail("Couldn't create \(path): \(String(cString: strerror(errno)))") }
    }

    var samples: Int64 { queue.sync { written } }
    var secondsSinceBuffer: Double { since(\.lastBuffer) }
    var secondsSinceSound: Double { since(\.lastSound) }

    private func since(_ field: KeyPath<Track, UInt64>) -> Double {
        stateLock.lock()
        let time = self[keyPath: field]
        stateLock.unlock()
        return hostSeconds(mach_absolute_time()) - hostSeconds(time)
    }

    /// Restart the stall and silence clocks, as if a buffer just arrived.
    func markAlive() {
        stateLock.lock()
        lastBuffer = mach_absolute_time()
        lastSound = lastBuffer
        stateLock.unlock()
    }

    func capture(_ input: UnsafePointer<AudioBufferList>, _ time: UnsafePointer<AudioTimeStamp>, rate: Double) {
        let buffers = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: input))
        if !described {
            described = true
            let layout = buffers.map { "\($0.mNumberChannels)ch/\($0.mDataByteSize)B" }.joined(separator: " ")
            FileHandle.standardError.write("\(name): first buffer at \(rate) Hz: [\(layout)]\n".data(using: .utf8)!)
        }
        guard let buffer = buffers.first, let data = buffer.mData, rate > 0 else { return }
        let channels = Int(max(buffer.mNumberChannels, 1))
        let frames = Int(buffer.mDataByteSize) / MemoryLayout<Float>.size / channels
        guard frames > 0 else { return }
        let source = data.bindMemory(to: Float.self, capacity: frames * channels)
        var mono = [Float](repeating: 0, count: frames)
        var sound = false
        for i in 0..<frames {
            mono[i] = source[i * channels]
            if mono[i] != 0 { sound = true }
        }
        let valid = time.pointee.mFlags.contains(.hostTimeValid)
        let hostTime = valid ? time.pointee.mHostTime : mach_absolute_time()
        stateLock.lock()
        lastBuffer = mach_absolute_time()
        if sound { lastSound = lastBuffer }
        stateLock.unlock()
        let start = origin(claiming: hostTime)
        queue.async { self.place(mono, rate: rate, at: hostSeconds(hostTime) - hostSeconds(start)) }
    }

    private func place(_ mono: [Float], rate: Double, at seconds: Double) {
        if converter == nil || converterRate != rate {
            let inputFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: rate, channels: 1, interleaved: false)!
            converter = AVAudioConverter(from: inputFormat, to: outputFormat)
            converter?.sampleRateConverterQuality = AVAudioQuality.high.rawValue
            converterRate = rate
        }
        guard let converter,
              let input = AVAudioPCMBuffer(pcmFormat: converter.inputFormat, frameCapacity: AVAudioFrameCount(mono.count)),
              let output = AVAudioPCMBuffer(pcmFormat: outputFormat, frameCapacity: AVAudioFrameCount(Double(mono.count) * sampleRate / rate) + 64)
        else { return }
        input.frameLength = AVAudioFrameCount(mono.count)
        mono.withUnsafeBufferPointer { input.floatChannelData![0].update(from: $0.baseAddress!, count: mono.count) }
        var supplied = false
        var error: NSError?
        converter.convert(to: output, error: &error) { _, status in
            if supplied {
                status.pointee = .noDataNow
                return nil
            }
            supplied = true
            status.pointee = .haveData
            return input
        }
        let count = Int(output.frameLength)
        var samples = [Int16](repeating: 0, count: count)
        let converted = output.floatChannelData![0]
        for i in 0..<count {
            samples[i] = Int16(max(-1, min(1, converted[i])) * 32767)
        }
        let expected = Int64(seconds * sampleRate)
        var first = 0
        if expected - written > toleranceSamples {
            writeSamples([Int16](repeating: 0, count: Int(expected - written)))
        } else if written - expected > toleranceSamples {
            first = Int(min(written - expected, Int64(count)))
        }
        if first < count { writeSamples(Array(samples[first...])) }
    }

    private func writeSamples(_ samples: [Int16]) {
        samples.withUnsafeBytes { buffer in
            var offset = 0
            while offset < buffer.count {
                let n = write(descriptor, buffer.baseAddress! + offset, buffer.count - offset)
                if n <= 0 { return }
                offset += n
            }
        }
        written += Int64(samples.count)
    }

    func finish() {
        queue.sync { _ = close(descriptor) }
    }
}

// MARK: - Capture state (main queue unless noted)

var tapID = AudioObjectID(kAudioObjectUnknown)
var tapDescription: CATapDescription?
var aggregateID = AudioObjectID(kAudioObjectUnknown)
var systemProc: AudioDeviceIOProcID?
var micDevice = AudioDeviceID(kAudioObjectUnknown)
var micProc: AudioDeviceIOProcID?
var micName = ""
var firstMicName = ""
var firstMicRate = 0.0
var micSwitches: [[String: Any]] = []
var micStallReported = false
var systemSilentReported = false
var stopping = false
var startedAt = Date()

let systemTrack = Track("system")
let micTrack = Track("mic")
let systemIOQueue = DispatchQueue(label: "io.system", qos: .userInteractive)
let micIOQueue = DispatchQueue(label: "io.mic", qos: .userInteractive)

func cleanUp() {
    if let proc = micProc {
        AudioDeviceStop(micDevice, proc)
        AudioDeviceDestroyIOProcID(micDevice, proc)
        micProc = nil
    }
    if let proc = systemProc {
        AudioDeviceStop(aggregateID, proc)
        AudioDeviceDestroyIOProcID(aggregateID, proc)
        systemProc = nil
    }
    if aggregateID != kAudioObjectUnknown {
        AudioHardwareDestroyAggregateDevice(aggregateID)
        aggregateID = AudioObjectID(kAudioObjectUnknown)
    }
    if tapID != kAudioObjectUnknown {
        AudioHardwareDestroyProcessTap(tapID)
        tapID = AudioObjectID(kAudioObjectUnknown)
    }
}

func startMic(_ device: AudioDeviceID) -> OSStatus {
    var rate = streamFormat(device, kAudioDevicePropertyStreamFormat, kAudioObjectPropertyScopeInput).mSampleRate
    if rate <= 0 {
        var nominal = Float64(0)
        var size = UInt32(MemoryLayout<Float64>.size)
        var a = address(kAudioDevicePropertyNominalSampleRate)
        AudioObjectGetPropertyData(device, &a, 0, nil, &size, &nominal)
        rate = nominal
    }
    var proc: AudioDeviceIOProcID?
    var status = AudioDeviceCreateIOProcIDWithBlock(&proc, device, micIOQueue) { _, input, time, _, _ in
        micTrack.capture(input, time, rate: rate)
    }
    guard status == noErr, let proc else { return status }
    status = AudioDeviceStart(device, proc)
    if status != noErr {
        AudioDeviceDestroyIOProcID(device, proc)
        return status
    }
    micDevice = device
    micProc = proc
    micName = readString(device, kAudioObjectPropertyName)
    if firstMicName.isEmpty {
        firstMicName = micName
        firstMicRate = rate
    }
    micTrack.markAlive()
    return noErr
}

func stopMic() {
    guard let proc = micProc else { return }
    AudioDeviceStop(micDevice, proc)
    AudioDeviceDestroyIOProcID(micDevice, proc)
    micProc = nil
}

/// Move to the current default input, or restart the same one after a stall.
func restartMic(reportingChange: Bool) {
    guard !stopping else { return }
    let device = defaultDevice(kAudioHardwarePropertyDefaultInputDevice)
    if reportingChange && device == micDevice && micProc != nil { return }
    let previous = micDevice
    stopMic()
    guard device != kAudioObjectUnknown else {
        emit(["event": "error", "message": "No microphone is available."])
        micTrack.markAlive()
        return
    }
    let status = startMic(device)
    if status != noErr {
        emit(["event": "error", "message": "Couldn't start the microphone \(readString(device, kAudioObjectPropertyName)) (Core Audio error \(status))."])
        micTrack.markAlive()
        return
    }
    if device != previous {
        let at = rounded(secondsSinceOrigin())
        micSwitches.append(["device": micName, "at": at])
        emit(["event": "mic_changed", "device": micName, "at": at])
    }
}

func shutdown() {
    guard !stopping else { return }
    stopping = true
    cleanUp()
    systemTrack.finish()
    micTrack.finish()
    let formatter = ISO8601DateFormatter()
    formatter.timeZone = .current
    let recording: [String: Any] = [
        "started": formatter.string(from: startedAt),
        "system_device": readString(builtInOutput(), kAudioObjectPropertyName),
        "mic_device": firstMicName,
        "mic_rate": firstMicRate,
        "system_samples": systemTrack.samples,
        "mic_samples": micTrack.samples,
        "mic_switches": micSwitches,
    ]
    if let data = try? JSONSerialization.data(withJSONObject: recording, options: [.prettyPrinted, .sortedKeys]) {
        try? data.write(to: directory.appendingPathComponent("recording.json"))
    }
    exit(0)
}

// MARK: - Start

let anchor = builtInOutput()
let anchorUID = readString(anchor, kAudioDevicePropertyDeviceUID)
let anchorName = readString(anchor, kAudioObjectPropertyName)
if anchorUID.isEmpty { fail("No output device to anchor the system audio tap on.") }

let excludedObject = processObject(for: excludedPID)
let tap = CATapDescription(monoGlobalTapButExcludeProcesses: excludedObject == kAudioObjectUnknown ? [] : [excludedObject])
tap.uuid = UUID()
tap.isPrivate = true
tap.muteBehavior = .unmuted
tapDescription = tap
var status = AudioHardwareCreateProcessTap(tap, &tapID)
if status != noErr { fail("Couldn't create the system audio tap (Core Audio error \(status)).") }
let tapFormat = streamFormat(tapID, kAudioTapPropertyFormat, kAudioObjectPropertyScopeGlobal)

let aggregate: [String: Any] = [
    kAudioAggregateDeviceNameKey: "STT Local meeting recorder",
    kAudioAggregateDeviceUIDKey: UUID().uuidString,
    kAudioAggregateDeviceMainSubDeviceKey: anchorUID,
    kAudioAggregateDeviceIsPrivateKey: true,
    kAudioAggregateDeviceIsStackedKey: false,
    kAudioAggregateDeviceTapAutoStartKey: true,
    kAudioAggregateDeviceSubDeviceListKey: [[kAudioSubDeviceUIDKey: anchorUID]],
    kAudioAggregateDeviceTapListKey: [[kAudioSubTapDriftCompensationKey: true, kAudioSubTapUIDKey: tap.uuid.uuidString]],
]
status = AudioHardwareCreateAggregateDevice(aggregate as CFDictionary, &aggregateID)
if status != noErr { fail("Couldn't create the private recording device (Core Audio error \(status)).") }

let firstMic = defaultDevice(kAudioHardwarePropertyDefaultInputDevice)
if firstMic == kAudioObjectUnknown { fail("No microphone is available.") }
startedAt = Date()
status = startMic(firstMic)
if status != noErr {
    fail("Couldn't start the microphone \(readString(firstMic, kAudioObjectPropertyName)) (Core Audio error \(status)).")
}

let tapRate = tapFormat.mSampleRate
status = AudioDeviceCreateIOProcIDWithBlock(&systemProc, aggregateID, systemIOQueue) { _, input, time, _, _ in
    systemTrack.capture(input, time, rate: tapRate)
}
if status != noErr { fail("Couldn't read the system audio tap (Core Audio error \(status)).") }
status = AudioDeviceStart(aggregateID, systemProc)
if status != noErr { fail("Couldn't start the system audio tap (Core Audio error \(status)).") }
systemTrack.markAlive()

emit(["event": "started", "system_device": anchorName, "mic_device": micName, "mic_rate": firstMicRate])

// MARK: - Listeners

var defaultInput = address(kAudioHardwarePropertyDefaultInputDevice)
AudioObjectAddPropertyListenerBlock(systemObject, &defaultInput, DispatchQueue.main) { _, _ in
    restartMic(reportingChange: true)
}

// STT Local may not have touched audio yet when recording starts, in which
// case it has no process object to exclude until it plays its first cue.
var processList = address(kAudioHardwarePropertyProcessObjectList)
var processListListener: AudioObjectPropertyListenerBlock?
if excludedObject == kAudioObjectUnknown && excludedPID > 0 {
    let listener: AudioObjectPropertyListenerBlock = { _, _ in
        let object = processObject(for: excludedPID)
        guard object != kAudioObjectUnknown, let tap = tapDescription, !stopping else { return }
        tap.processes = [object]
        var description = address(kAudioTapPropertyDescription)
        var value: CATapDescription = tap
        let size = UInt32(MemoryLayout<CATapDescription>.size)
        if AudioObjectSetPropertyData(tapID, &description, 0, nil, size, &value) == noErr,
           let registered = processListListener {
            AudioObjectRemovePropertyListenerBlock(systemObject, &processList, DispatchQueue.main, registered)
            processListListener = nil
        }
    }
    processListListener = listener
    AudioObjectAddPropertyListenerBlock(systemObject, &processList, DispatchQueue.main, listener)
}

let watchdog = DispatchSource.makeTimerSource(queue: .main)
watchdog.schedule(deadline: .now() + 1, repeating: 1)
watchdog.setEventHandler {
    guard !stopping else { return }
    if micTrack.secondsSinceBuffer > micStallSeconds {
        if !micStallReported {
            micStallReported = true
            emit(["event": "mic_stalled", "at": rounded(secondsSinceOrigin())])
        }
        restartMic(reportingChange: false)
    } else if micTrack.secondsSinceBuffer < 1 {
        micStallReported = false
    }
    if !systemSilentReported && systemTrack.secondsSinceSound > systemSilentSeconds && othersPlaying() {
        systemSilentReported = true
        emit(["event": "system_silent", "at": rounded(max(0, secondsSinceOrigin() - systemTrack.secondsSinceSound))])
    }
}
watchdog.resume()

// MARK: - Stopping

for stopSignal in [SIGTERM, SIGINT] {
    signal(stopSignal, SIG_IGN)
}
let signalSources = [SIGTERM, SIGINT].map { stopSignal -> DispatchSourceSignal in
    let source = DispatchSource.makeSignalSource(signal: stopSignal, queue: .main)
    source.setEventHandler { shutdown() }
    source.resume()
    return source
}

Thread {
    var buffer = [UInt8](repeating: 0, count: 4096)
    while read(0, &buffer, buffer.count) > 0 {}
    DispatchQueue.main.async { shutdown() }
}.start()

dispatchMain()
