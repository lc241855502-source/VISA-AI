import streamlit as st
import os
import json
import uuid
import re
from datetime import datetime, date
import pandas as pd
from PIL import Image
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header

# ==================== 基础配置 ====================
st.set_page_config(page_title="签证资料收集系统", page_icon="🛂", layout="wide")

DATA_DIR = "data"
UPLOAD_DIR = "uploads"
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

# 密钥配置（部署时在Streamlit Cloud Secrets中设置）
ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD", "admin123")
SMTP_HOST = st.secrets.get("SMTP_HOST", "smtp.qq.com")
SMTP_PORT = st.secrets.get("SMTP_PORT", 465)
SMTP_USER = st.secrets.get("SMTP_USER", "")  # 发件人邮箱
SMTP_PASS = st.secrets.get("SMTP_PASS", "")  # 发件人邮箱授权码
NOTICE_EMAIL = st.secrets.get("NOTICE_EMAIL", "139030992@qq.com")  # 收件人

# 签证类型配置
VISA_TYPES = [
    "美国B1/B2签证",
    "申根签证",
    "澳大利亚600签证",
    "加拿大访问签证",
    "英国标准访客签证"
]

# ==================== OCR护照识别模块 ====================
@st.cache_resource(show_spinner=False)
def load_ocr_model():
    """加载OCR模型（仅英文数字，识别护照MRZ码）"""
    import easyocr
    return easyocr.Reader(['en'], gpu=False)

def parse_mrz(mrz_lines):
    """解析护照MRZ机读码，返回个人信息"""
    result = {}
    if len(mrz_lines) < 2:
        return result
    
    line1 = mrz_lines[0].strip().replace(' ', '')
    line2 = mrz_lines[1].strip().replace(' ', '')
    
    # 解析第二行（核心信息）
    if len(line2) >= 44:
        # 护照号
        result['passport_no'] = line2[0:9].replace('<', '')
        # 出生日期 YYMMDD
        birth_str = line2[13:19]
        try:
            year = int(birth_str[0:2])
            year += 1900 if year > 20 else 2000
            result['birth_date'] = f"{year}-{birth_str[2:4]}-{birth_str[4:6]}"
        except:
            pass
        # 性别
        result['gender'] = '男' if line2[20] == 'M' else '女' if line2[20] == 'F' else ''
        # 护照有效期 YYMMDD
        expiry_str = line2[21:27]
        try:
            year = int(expiry_str[0:2])
            year += 1900 if year > 20 else 2000
            result['expiry_date'] = f"{year}-{expiry_str[2:4]}-{expiry_str[4:6]}"
        except:
            pass
    
    # 解析第一行（姓名）
    if len(line1) >= 44:
        name_part = line1[5:].split('<<')[0]
        names = name_part.split('<')
        if len(names) >= 2:
            result['last_name_en'] = names[0].title()
            result['first_name_en'] = ''.join(names[1:]).title()
            result['full_name_en'] = f"{result['last_name_en']} {result['first_name_en']}"
    
    return result

def recognize_passport(image):
    """识别护照图片并提取信息"""
    try:
        reader = load_ocr_model()
        img = Image.open(image)
        import numpy as np
        img_np = np.array(img)
        
        # OCR识别
        results = reader.readtext(img_np, detail=0, paragraph=False)
        
        # 筛选MRZ行（包含<<的行）
        mrz_lines = []
        for line in results:
            if '<<' in line and len(line) > 30:
                clean_line = re.sub(r'[^A-Z0-9<]', '', line.upper())
                mrz_lines.append(clean_line)
        
        # 取最长的两行
        mrz_lines = sorted(mrz_lines, key=len, reverse=True)[:2]
        return parse_mrz(mrz_lines), mrz_lines
    except Exception as e:
        return {}, []

# ==================== 邮件通知模块 ====================
def send_notice_email(app_data):
    """发送新申请通知邮件"""
    if not SMTP_USER or not SMTP_PASS:
        return False, "未配置发件邮箱信息"
    
    try:
        msg = MIMEMultipart()
        msg['From'] = Header(f"签证资料系统 <{SMTP_USER}>")
        msg['To'] = NOTICE_EMAIL
        msg['Subject'] = Header(f"新签证申请 - {app_data['basic_info']['name_cn']} - {app_data['visa_type']}", 'utf-8')
        
        basic = app_data['basic_info']
        travel = app_data['travel_info']
        work = app_data['work_info']
        family = app_data['family_info']
        
        html = f"""
        <h3>新签证申请提交</h3>
        <p><b>申请编号：</b>{app_data['application_id']}</p>
        <p><b>提交时间：</b>{app_data['submit_time']}</p>
        <p><b>签证类型：</b>{app_data['visa_type']}</p>
        
        <h4>一、申请人基本信息</h4>
        <p>中文姓名：{basic['name_cn']}</p>
        <p>英文姓名：{basic['name_en']}</p>
        <p>性别：{basic['gender']}</p>
        <p>出生日期：{basic['birth_date']}</p>
        <p>护照号码：{basic['passport_no']}</p>
        <p>护照有效期至：{basic['passport_expiry']}</p>
        <p>联系电话：{basic['phone']}</p>
        <p>电子邮箱：{basic['email']}</p>
        
        <h4>二、出行信息</h4>
        <p>预计出发日期：{travel['depart_date']}</p>
        <p>是否有目标航班：{travel['has_flight']}</p>
        <p>航班信息：{travel.get('flight_detail', '无')}</p>
        <p>行程计划：{travel['itinerary']}</p>
        <p>近5年旅游记录：{travel['travel_history']}</p>
        
        <h4>三、工作信息</h4>
        <p>公司名称：{work['company']}</p>
        <p>职位：{work['position']}</p>
        <p>月薪资：{work['salary']}</p>
        
        <h4>四、家属信息</h4>
        <p>{family.get('family_detail', '无')}</p>
        """
        
        msg.attach(MIMEText(html, 'html', 'utf-8'))
        
        server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT)
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, NOTICE_EMAIL, msg.as_string())
        server.quit()
        return True, "邮件发送成功"
    except Exception as e:
        return False, str(e)

# ==================== 数据存储工具 ====================
def save_application(data, files):
    app_id = str(uuid.uuid4())[:8]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    app_folder = os.path.join(UPLOAD_DIR, f"{timestamp}_{app_id}")
    os.makedirs(app_folder, exist_ok=True)
    
    file_paths = {}
    for key, file in files.items():
        if file is not None:
            file_path = os.path.join(app_folder, f"{key}_{file.name}")
            with open(file_path, "wb") as f:
                f.write(file.getbuffer())
            file_paths[key] = file_path
    
    data["application_id"] = app_id
    data["submit_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data["files"] = file_paths
    
    json_path = os.path.join(DATA_DIR, f"{timestamp}_{app_id}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    return app_id

def load_all_applications():
    apps = []
    for filename in sorted(os.listdir(DATA_DIR), reverse=True):
        if filename.endswith(".json"):
            with open(os.path.join(DATA_DIR, filename), "r", encoding="utf-8") as f:
                apps.append(json.load(f))
    return apps

# ==================== 用户提交页面 ====================
def user_submission_page():
    st.title("🛂 签证资料收集系统")
    st.markdown("请按步骤填写信息并上传材料，带 * 为必填项")
    
    visa_type = st.selectbox("选择签证类型 *", VISA_TYPES)
    st.divider()
    
    # 护照上传与识别
    st.subheader("一、护照信息")
    passport_file = st.file_uploader("上传护照首页照片 *", type=["jpg", "jpeg", "png"], key="passport_upload")
    
    col_btn1, col_btn2 = st.columns([1, 5])
    with col_btn1:
        recognize_btn = st.button("🔍 一键识别信息", disabled=passport_file is None)
    
    if 'ocr_result' not in st.session_state:
        st.session_state.ocr_result = {}
    
    if recognize_btn and passport_file:
        with st.spinner("正在识别护照信息..."):
            info, mrz = recognize_passport(passport_file)
            st.session_state.ocr_result = info
            if info:
                st.success("识别成功！信息已自动填充下方，可手动修正")
            else:
                st.warning("未能识别到完整信息，请手动填写")
    
    st.markdown("---")
    
    col1, col2 = st.columns(2)
    ocr = st.session_state.ocr_result
    
    with col1:
        name_cn = st.text_input("中文姓名 *", value="")
        name_en = st.text_input("英文姓名（拼音大写）*", value=ocr.get('full_name_en', ''))
        gender = st.selectbox("性别 *", ["男", "女"], index=0 if ocr.get('gender') == '男' else 1 if ocr.get('gender') == '女' else 0)
        
        birth_default = ocr.get('birth_date', '1990-01-01')
        try:
            birth_date = st.date_input("出生日期 *", value=date.fromisoformat(birth_default))
        except:
            birth_date = st.date_input("出生日期 *")
    
    with col2:
        passport_no = st.text_input("护照号码 *", value=ocr.get('passport_no', ''))
        expiry_default = ocr.get('expiry_date', '2030-01-01')
        try:
            passport_expiry = st.date_input("护照有效期至 *", value=date.fromisoformat(expiry_default))
        except:
            passport_expiry = st.date_input("护照有效期至 *")
        phone = st.text_input("手机号码 *")
        email = st.text_input("电子邮箱 *")
    
    st.divider()
    
    # 出行信息
    st.subheader("二、出行信息")
    col3, col4 = st.columns(2)
    with col3:
        depart_date = st.date_input("预计出发日期 *")
        has_flight = st.selectbox("是否有目标航班 *", ["否", "是"])
        flight_detail = st.text_input("航班号/航线信息", disabled=(has_flight == "否"))
    
    with col4:
        itinerary = st.text_area("行程计划简述 *", placeholder="如：洛杉矶-拉斯维加斯-旧金山 10天自驾游，无则填'无'")
        travel_history = st.text_area("近5年出境旅游记录 *", placeholder="例：2023.07 日本；2024.02 泰国；较多则只填欧美澳新国家")
    
    st.divider()
    
    # 工作信息
    st.subheader("三、工作信息")
    col5, col6, col7 = st.columns(3)
    with col5:
        company = st.text_input("公司名称 *")
    with col6:
        position = st.text_input("职位 *")
    with col7:
        salary = st.text_input("月薪资（元）*")
    
    st.divider()
    
    # 家属信息
    st.subheader("四、父母/子女信息")
    family_detail = st.text_area(
        "请填写父母及子女的姓名、出生日期、职位、月薪资 *",
        placeholder="例：\n父亲：张三，1965-03-15，退休，退休金4000\n母亲：李四，1967-08-20，退休，退休金3800\n子女：张小明，2015-09-01，学生，无收入"
    )
    
    st.divider()
    
    # 专项材料
    st.subheader("五、专项材料上传")
    extra_files = {}
    
    if visa_type == "美国B1/B2签证":
        extra_files['photo_us'] = st.file_uploader(
            "51×51mm 白底电子证件照（近6个月）*",
            type=["jpg", "jpeg", "png"],
            key="photo_us"
        )
        st.caption("要求：正方形、白底、免冠、露双耳，不能戴眼镜")
    
    elif visa_type == "澳大利亚600签证":
        extra_files['hukou'] = st.file_uploader(
            "户口本全本扫描件 *",
            type=["jpg", "jpeg", "png", "pdf"],
            key="hukou"
        )
        st.caption("请上传户口本所有页的扫描件或清晰照片")
    
    else:
        st.info("该签证类型无额外专项材料，确认信息无误后即可提交")
    
    st.divider()
    
    # 提交按钮
    if st.button("✅ 提交申请", type="primary", use_container_width=True):
        missing = []
        if not all([name_cn, name_en, passport_no, phone, email]):
            missing.append("基本信息带*项")
        if not company or not position or not salary:
            missing.append("工作信息带*项")
        if not itinerary or not travel_history or not family_detail:
            missing.append("出行/家属信息带*项")
        if passport_file is None:
            missing.append("护照首页照片")
        
        if visa_type == "美国B1/B2签证" and extra_files.get('photo_us') is None:
            missing.append("美国签证电子照片")
        if visa_type == "澳大利亚600签证" and extra_files.get('hukou') is None:
            missing.append("户口本扫描件")
        
        if missing:
            st.error(f"请完善以下必填项：{', '.join(missing)}")
        else:
            all_files = {"passport": passport_file}
            all_files.update(extra_files)
            
            application_data = {
                "visa_type": visa_type,
                "basic_info": {
                    "name_cn": name_cn,
                    "name_en": name_en.upper(),
                    "gender": gender,
                    "birth_date": str(birth_date),
                    "passport_no": passport_no,
                    "passport_expiry": str(passport_expiry),
                    "phone": phone,
                    "email": email,
                },
                "travel_info": {
                    "depart_date": str(depart_date),
                    "has_flight": has_flight,
                    "flight_detail": flight_detail if has_flight == "是" else "",
                    "itinerary": itinerary,
                    "travel_history": travel_history,
                },
                "work_info": {
                    "company": company,
                    "position": position,
                    "salary": salary,
                },
                "family_info": {
                    "family_detail": family_detail
                }
            }
            
            app_id = save_application(application_data, all_files)
            
            with st.spinner("正在发送通知邮件..."):
                success, msg = send_notice_email(application_data)
            
            st.success(f"🎉 提交成功！申请编号：**{app_id}**")
            if success:
                st.toast("邮件通知已发送", icon="📧")
            else:
                st.warning(f"邮件发送失败：{msg}，但申请已保存")
            
            st.session_state.ocr_result = {}

# ==================== 管理员后台 ====================
def admin_page():
    st.title("🔐 管理员后台")
    
    if "admin_logged_in" not in st.session_state:
        st.session_state.admin_logged_in = False
    
    if not st.session_state.admin_logged_in:
        password = st.text_input("请输入管理员密码", type="password")
        if st.button("登录"):
            if password == ADMIN_PASSWORD:
                st.session_state.admin_logged_in = True
                st.rerun()
            else:
                st.error("密码错误")
        return
    
    apps = load_all_applications()
    st.sidebar.write(f"共 {len(apps)} 份申请")
    
    visa_filter = st.sidebar.multiselect(
        "按签证类型筛选",
        VISA_TYPES,
        default=VISA_TYPES
    )
    
    filtered = [a for a in apps if a["visa_type"] in visa_filter]
    
    st.subheader(f"申请列表（共 {len(filtered)} 条）")
    
    if filtered:
        summary = []
        for app in filtered:
            summary.append({
                "申请编号": app["application_id"],
                "提交时间": app["submit_time"],
                "签证类型": app["visa_type"],
                "姓名": app["basic_info"]["name_cn"],
                "护照号": app["basic_info"]["passport_no"],
                "手机号": app["basic_info"]["phone"],
                "出发日期": app["travel_info"]["depart_date"],
            })
        
        df = pd.DataFrame(summary)
        st.dataframe(df, use_container_width=True, hide_index=True)
        
        st.divider()
        
        selected_id = st.selectbox(
            "选择申请编号查看详情",
            [a["application_id"] for a in filtered]
        )
        
        selected = next(a for a in filtered if a["application_id"] == selected_id)
        basic = selected["basic_info"]
        travel = selected["travel_info"]
        work = selected["work_info"]
        
        st.subheader(f"申请详情 - {basic['name_cn']}")
        
        tab1, tab2, tab3, tab4 = st.tabs(["基本信息", "出行&工作", "家属信息", "上传材料"])
        
        with tab1:
            col_a, col_b = st.columns(2)
            with col_a:
                st.write(f"**中文姓名：** {basic['name_cn']}")
                st.write(f"**英文姓名：** {basic['name_en']}")
                st.write(f"**性别：** {basic['gender']}")
                st.write(f"**出生日期：** {basic['birth_date']}")
            with col_b:
                st.write(f"**护照号码：** {basic['passport_no']}")
                st.write(f"**护照有效期：** {basic['passport_expiry']}")
                st.write(f"**手机：** {basic['phone']}")
                st.write(f"**邮箱：** {basic['email']}")
        
        with tab2:
            st.write(f"**预计出发：** {travel['depart_date']}")
            st.write(f"**目标航班：** {travel['has_flight']} {travel.get('flight_detail','')}")
            st.write(f"**行程计划：** {travel['itinerary']}")
            st.write(f"**旅游记录：** {travel['travel_history']}")
            st.divider()
            st.write(f"**公司：** {work['company']}")
            st.write(f"**职位：** {work['position']}")
            st.write(f"**月薪资：** {work['salary']}")
        
        with tab3:
            st.text(selected["family_info"]["family_detail"])
        
        with tab4:
            file_cols = st.columns(2)
            idx = 0
            for key, path in selected["files"].items():
                with file_cols[idx % 2]:
                    st.markdown(f"**{key}**")
                    if path.lower().endswith(('.jpg', '.jpeg', '.png')):
                        try:
                            img = Image.open(path)
                            st.image(img, use_column_width=True)
                        except:
                            st.write(f"文件：{os.path.basename(path)}")
                    else:
                        st.write(f"📄 {os.path.basename(path)}")
                        with open(path, "rb") as f:
                            st.download_button("下载", f, file_name=os.path.basename(path), key=f"dl_{key}")
                idx += 1
    else:
        st.info("暂无申请记录")

# ==================== 主入口 ====================
def main():
    mode = st.sidebar.radio("页面模式", ["用户提交", "管理员后台"])
    
    if mode == "用户提交":
        user_submission_page()
    else:
        admin_page()
    
    st.sidebar.divider()
    st.sidebar.caption("签证资料收集系统 v2.0")

if __name__ == "__main__":
    main()