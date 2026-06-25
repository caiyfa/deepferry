package com.deepferry.examples.financialmock.common;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import java.util.List;

@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
public class ApiResponse<T> {

    private List<T> data;
    private long total;

    public static <T> ApiResponse<T> of(List<T> data) {
        return ApiResponse.<T>builder()
                .data(data)
                .total(data.size())
                .build();
    }
}
