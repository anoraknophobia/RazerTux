/* Excerpt of razermouse_driver.c (OpenRazer, GPL-2.0) for tests */
static ssize_t razer_attr_write_poll_rate(struct device *dev, struct device_attribute *attr, const char *buf, size_t count)
{
    struct razer_mouse_device *device = dev_get_drvdata(dev);
    struct razer_report request = {0};
    unsigned short polling_rate;

    switch(device->usb_pid) {
    case USB_DEVICE_ID_RAZER_DEATHADDER_3_5G:
        deathadder3_5g_set_poll_rate(device, polling_rate);
        return count;

    case USB_DEVICE_ID_RAZER_DEATHADDER_ELITE:
    /* a comment with case USB_DEVICE_ID_RAZER_NAGA_V2_PRO_WIRED: inside */
        request = razer_chroma_misc_set_polling_rate(polling_rate);
        request.transaction_id.id = 0x3f;
        break;

    case USB_DEVICE_ID_RAZER_NAGA_V2_PRO_WIRED:
    case USB_DEVICE_ID_RAZER_NAGA_V2_PRO_WIRELESS:
        request = razer_chroma_misc_set_polling_rate2(polling_rate, 0x00);
        request.transaction_id.id = 0x1f;
        break;

    default:
        request = razer_chroma_misc_set_polling_rate(polling_rate);
        request.transaction_id.id = 0xFF;
        break;
    }
    return count;
}

static ssize_t razer_attr_read_charge_level(struct device *dev, struct device_attribute *attr, char *buf)
{
    struct razer_report request = {0};

    request = razer_chroma_misc_get_battery_level();

    switch (device->usb_pid) {
    case USB_DEVICE_ID_RAZER_NAGA_V2_PRO_WIRED:
        request.transaction_id.id = 0x1f;
        break;
    }
    return 0;
}

static int razer_mouse_probe(struct hid_device *hdev, const struct hid_device_id *id)
{
    CREATE_DEVICE_FILE(&hdev->dev, &dev_attr_device_mode);
    switch(dev->usb_pid) {
    case USB_DEVICE_ID_RAZER_NAGA_V2_PRO_WIRELESS:
        CREATE_DEVICE_FILE(&hdev->dev, &dev_attr_charge_effect);
        fallthrough;
    case USB_DEVICE_ID_RAZER_NAGA_V2_PRO_WIRED:
        CREATE_DEVICE_FILE(&hdev->dev, &dev_attr_dpi);
        CREATE_DEVICE_FILE(&hdev->dev, &dev_attr_logo_led_brightness);
        break;
    }
    return 0;
}

static ssize_t razer_attr_write_matrix_effect_static(struct device *dev, struct device_attribute *attr, const char *buf, size_t count)
{
    switch (device->usb_pid) {
    case USB_DEVICE_ID_RAZER_NAGA_V2_PRO_WIRED:
    case USB_DEVICE_ID_RAZER_NAGA_V2_PRO_WIRELESS:
        request = get_razer_report(0x0F, 0x02, 0x06);
        request.arguments[2] = 0x08;
        request.transaction_id.id = 0x1f;
        err = razer_send_payload(device, &request, &response);

        request = razer_chroma_standard_set_led_state(VARSTORE, led_id, true);
        request.transaction_id.id = 0x1f;
        err = razer_send_payload(device, &request, &response);

        request = razer_chroma_standard_set_led_rgb(VARSTORE, led_id, (struct razer_rgb*)&buf[0]);
        request.transaction_id.id = 0x1f;
        break;

    case USB_DEVICE_ID_RAZER_DEATHADDER_ELITE:
        request = razer_chroma_standard_matrix_effect_static((struct razer_rgb*)&buf[0]);
        break;

    default:
        return -EINVAL;
    }
    return count;
}
