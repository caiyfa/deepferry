package com.deepferry.examples.financialmock.auth;

import com.deepferry.examples.financialmock.auth.dto.LoginRequest;
import com.deepferry.examples.financialmock.auth.dto.LoginResponse;
import com.deepferry.examples.financialmock.auth.dto.RefreshRequest;
import jakarta.validation.Valid;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.ResponseEntity;
import org.springframework.security.authentication.BadCredentialsException;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

@RestController
@RequestMapping("/auth")
public class AuthController {

    private final JwtService jwtService;
    private final String defaultUser;
    private final String defaultPassword;

    public AuthController(
            JwtService jwtService,
            @Value("${app.auth.default-user}") String defaultUser,
            @Value("${app.auth.default-password}") String defaultPassword) {
        this.jwtService = jwtService;
        this.defaultUser = defaultUser;
        this.defaultPassword = defaultPassword;
    }

    @PostMapping("/login")
    public ResponseEntity<?> login(@Valid @RequestBody LoginRequest request) {
        if (!defaultUser.equals(request.getUsername()) || !defaultPassword.equals(request.getPassword())) {
            throw new BadCredentialsException("Invalid username or password");
        }

        String accessToken = jwtService.generateAccessToken(request.getUsername());
        String refreshToken = jwtService.generateRefreshToken(request.getUsername());

        LoginResponse response = LoginResponse.builder()
                .accessToken(accessToken)
                .refreshToken(refreshToken)
                .tokenType("Bearer")
                .expiresIn(jwtService.getExpiresIn(accessToken))
                .build();

        return ResponseEntity.ok(response);
    }

    @PostMapping("/refresh")
    public ResponseEntity<?> refresh(@Valid @RequestBody RefreshRequest request) {
        if (!jwtService.isTokenValid(request.getRefreshToken(), "refresh")) {
            throw new BadCredentialsException("Invalid or expired refresh token");
        }

        String username = jwtService.extractUsername(request.getRefreshToken());
        String newAccessToken = jwtService.generateAccessToken(username);

        LoginResponse response = LoginResponse.builder()
                .accessToken(newAccessToken)
                .refreshToken(request.getRefreshToken())
                .tokenType("Bearer")
                .expiresIn(jwtService.getExpiresIn(newAccessToken))
                .build();

        return ResponseEntity.ok(response);
    }
}
