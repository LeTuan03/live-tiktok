// Where to find the server URL and stream key on each platform, for the
// "Kênh phát" page. Pure data. The menus are described in general terms on
// purpose: TikTok and Shopee rename them often, and which accounts may
// stream from software at all varies by region and account.

import type { Platform } from "./types";

export interface PlatformGuide {
  label: string;
  /** Filled in for a new destination when the platform uses one fixed server. */
  defaultServerUrl: string;
  serverPlaceholder: string;
  /** The platform always gives a key apart from the server URL. */
  keyRequired: boolean;
  steps: string[];
  note?: string;
}

export const PLATFORM_ORDER: Platform[] = ["tiktok", "shopee", "youtube", "custom"];

export const PLATFORM_GUIDES: Record<Platform, PlatformGuide> = {
  tiktok: {
    label: "TikTok Live",
    defaultServerUrl: "",
    serverPlaceholder: "rtmp://push-rtmp-....tiktokcdn.com/game/",
    keyRequired: true,
    steps: [
      "Trên máy tính, mở TikTok LIVE Producer (livecenter.tiktok.com/producer) hoặc TikTok LIVE Studio.",
      "Nhập tiêu đề, chủ đề cho phiên LIVE rồi tạo phiên phát bằng phần mềm (stream key).",
      "Sao chép Server URL và Stream key, dán vào đây rồi lưu.",
      "Trong phòng điều khiển, bật kênh này và bấm Bắt đầu. Khi kênh báo Đang phát, quay lại TikTok bấm Go LIVE.",
    ],
    note: "Tài khoản phải đủ điều kiện LIVE và được TikTok cấp stream key (thường cần 1.000 người theo dõi trở lên). Key thường đổi sau mỗi phiên.",
  },
  shopee: {
    label: "Shopee Live",
    defaultServerUrl: "",
    serverPlaceholder: "rtmp://...",
    keyRequired: true,
    steps: [
      "Vào Kênh Người Bán (Seller Centre), mục Shopee Live, tạo phiên livestream và thêm sản phẩm.",
      "Chọn phát bằng phần mềm (OBS, RTMP) để Shopee hiện Server URL và Stream key.",
      "Sao chép hai giá trị đó, dán vào đây rồi lưu.",
      "Trong phòng điều khiển, bật kênh này và bấm Bắt đầu. Khi Shopee đã thấy hình, bấm Bắt đầu phát trong Seller Centre.",
    ],
    note: "Mỗi phiên Shopee Live có key riêng: tạo phiên mới thì dán lại key mới trước khi phát.",
  },
  youtube: {
    label: "YouTube Live",
    defaultServerUrl: "rtmp://a.rtmp.youtube.com/live2",
    serverPlaceholder: "rtmp://a.rtmp.youtube.com/live2",
    keyRequired: true,
    steps: [
      "Mở YouTube Studio, chọn Phát trực tiếp.",
      "Sao chép Khóa sự kiện phát trực tiếp (stream key) và dán vào đây. Server URL đã điền sẵn.",
    ],
  },
  custom: {
    label: "RTMP khác",
    defaultServerUrl: "",
    serverPlaceholder: "rtmp://máy-chủ/ứng-dụng",
    keyRequired: false,
    steps: [
      "Dán địa chỉ RTMP (bắt đầu bằng rtmp:// hoặc rtmps://) và stream key mà nền tảng cung cấp.",
      "Nếu nền tảng chỉ đưa một đường dẫn đầy đủ đã gồm key, dán cả đường dẫn vào Server URL và để trống key.",
    ],
  },
};
