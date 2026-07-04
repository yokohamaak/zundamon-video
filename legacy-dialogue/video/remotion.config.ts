import { Config } from "@remotion/cli/config";

// 静止背景＋キャラ＋字幕中心なのでjpegで十分（軽量・高速）
Config.setVideoImageFormat("jpeg");
Config.setOverwriteOutput(true);
